# kiroku の使い方

kiroku は、Kiro CLI（V3）での開発中に起きた失敗や手直しを記録し、ハーネスの改善案を出すための仕組みです。
ハーネスとは、`.kiro/steering`、`.kiro/hooks`、`.kiro/skills`、`.kiro/agents`、関連するスクリプトのことです。

いまの版は「記録」と「手動の振り返り」までです。
分類や PR 作成の自動化は、2週間分の記録を見てから設計します。

## 仕組み

1. **記録**：フックが、ツール実行のたびに `.kiro/retro/events.jsonl` へ追記します。LLM は使いません。
2. **集約**：`aggregate.py` が記録を読み、テスト失敗を後から分類し、期間ごとに要約します。
3. **振り返り**：専用エージェントの新しいセッションで `/kiroku-retro` を呼ぶと、レポートが `.kiro/retro/reports/` に出ます。

`.kiro/retro/` は `.gitignore` で除外されます。
コードや認証情報が混ざりうるので、コミットや PR に載せないでください。

## 必要なもの

- Kiro CLI 3.0 以降（3.2.1 で確認）
- python3（3.9 以降。追加のパッケージは不要）
- git

## 導入

```bash
git clone https://github.com/yuyumama/kiroku ~/kiroku
```

```bash
sh ~/kiroku/install.sh <導入先のリポジトリ>
```

導入先に置かれるファイルは次のとおりです。これらはコミットして構いません。

| ファイル | 役割 |
|---|---|
| `.kiro/hooks/kiroku.json` | 記録フック2本（PostToolUse、PostFileSave） |
| `.kiro/kiroku/record.py` | フックから呼ばれ、イベントを追記する |
| `.kiro/kiroku/aggregate.py` | 集約と分類 |
| `.kiro/kiroku/excerpt.py` | セッションログの該当箇所を抜き出す |
| `.kiro/kiroku/config.json` | 導入先ごとの設定 |
| `.kiro/skills/kiroku-retro/SKILL.md` | 振り返りの手順 |
| `.kiro/agents/kiroku-retro.json` | 振り返り専用のエージェント定義 |

既存のファイルは上書きしません。同じ名前で中身の違うファイルがあれば、何も変更せずに止まります。
kiroku を新しい版にするときは `--update` を付けます。`config.json` は置き換えません。

```bash
sh ~/kiroku/install.sh <導入先のリポジトリ> --update
```

導入後、Kiro を起動して `/hooks` を入力し、`kiroku-record-tool` と `kiroku-record-file` が出ることを確かめてください。

## 記録されるもの

設定なしで記録されるのは次の3種類です。

| 種類 | 記録する場面 | 主な項目 |
|---|---|---|
| `tool_failure` | シェル実行が 0 以外の終了コードで終わった | コマンド、終了コード、出力の末尾 |
| `tool_cancelled` | 人がツールの実行を取り消した | コマンド |
| `tool_denied` | 権限の規則でツールの実行が拒否された | コマンド |
| `write` | エージェントがファイルを保存した | パス、内容ハッシュ |

成功したシェル実行は記録しません。kiroku 自身のスクリプトの実行も記録しません。
1回の書き込みは、PostFileSave と PostToolUse の両方で記録されます（整形フックが書き換えた後の内容を取り直すため）。
集約では1回として数えます。
どのイベントにも、`session_id` と時刻（`ts`）が付きます。

コマンドと出力は、秘匿情報らしい値（トークン、パスワード、秘密鍵など）を伏せてから保存します。
伏せ字は完全ではありません。

## テスト結果も記録する

テスト結果を記録するには、`.kiro/kiroku/config.json` の `tests` に、コマンドとレポートの対応を書きます。

```json
{
  "tests": [
    { "match": "\\bpytest\\b", "report": "reports/junit.xml", "format": "junit" }
  ]
}
```

- `match`：テスト実行とみなすコマンドの正規表現です。
- `report`：JUnit XML の場所です。リポジトリのルートからの相対パスで、`**` などの glob が使えます。
- `format`：いまは `junit` だけです。

テストツール側で、**毎回 JUnit XML を出す設定**にしておく必要があります。
エージェントがオプションを付け忘れても記録できるよう、コマンドの引数ではなく設定ファイルで指定するのが確実です。

| ツール | 設定の例 |
|---|---|
| pytest | `pytest.ini` の `addopts = --junitxml=reports/junit.xml` |
| Jest | `jest-junit` をレポーターに追加し、`addFileAttribute` を有効にする |
| Vitest | `reporters: ['default', 'junit']` と `outputFile` |
| Go | `gotestsum --junitfile reports/junit.xml` |
| Maven、Gradle | 既定で出る（`target/surefire-reports/*.xml`、`build/test-results/**/*.xml`） |

設定すると、テスト実行ごとに `test_run` が記録されます。
項目は、テストID単位の成否、その時点のコミットハッシュ、作業ツリーの変更ファイルと内容ハッシュです。

記録の時点では分類しません。TDD のフェーズ宣言も、テストコマンドのブロックもしません。

## 集約する

```bash
python3 .kiro/kiroku/aggregate.py
```

既定では直近14日を、週ごとに要約します。

| オプション | 意味 |
|---|---|
| `--since 30d`、`--since 2026-11-01` | 開始。日数または日付。`all` で全期間 |
| `--until 2026-12-01` | 終了 |
| `--by day`、`--by week`、`--by all` | 要約の単位 |
| `--json` | JSON で出力 |

### テスト失敗の分類

| 分類 | 条件 |
|---|---|
| 正常な Red | 前回の全テスト成功より後に追加されたテスト。または、それ以降にテストファイルが変更されたテスト |
| 回帰 | 前回の全テスト成功の時点で存在し、通っていて、テストファイルが変わっていないテスト |
| その他 | 基準になる全テスト成功が無い、通った記録が無い、git から当時の状態を復元できない |

テストとファイルの対応は、レポートの `file` 属性があればそれを使います。
無ければ `classname`（`tests.test_foo.TestBar` など）からパスを推測します。
対応が分からない場合は、通っていたテストの失敗を回帰として扱い、その旨を根拠に書きます。

### 人間の手直し候補

エージェントが最後に書いた内容と、いまの内容が違うファイルです。
merge、pull、rebase、checkout、reset などで変わったファイルは除きます。

次の場合は検出できません。

- 人が直した後に、エージェントが同じファイルをもう一度書いた。
- エージェントがシェル経由（`sed` など）で書いた。

## 振り返る

作業中のセッションとは別に、専用エージェントで新しいセッションを起動します。

```bash
kiro-cli chat --agent kiroku-retro
```

起動したら、次を入力します。

```
/kiroku-retro
```

期間を変えるときは、後ろに書きます（例：`/kiroku-retro 直近30日`）。

レポートは `.kiro/retro/reports/YYYY-MM-DD-HHMM.md` に出ます。
中身は「繰り返し出ている型」と「ハーネスへの改善案」で、学びがなければ「なし」と書かれます。

専用エージェントは、次のことしかできません。

- 書き込み：`.kiro/retro/reports/` 配下だけ
- シェル：`aggregate.py` と `excerpt.py` は確認なしで実行でき、それ以外は実行のたびに人の確認が要る

ハーネスの変更は、レポートを読んだ人が行います。

## 動作確認

社内リポジトリに入れる前に、付属のサンプルで一通り試せます。追加のパッケージは要りません。

```bash
mkdir ~/kiroku-demo && cp -R ~/kiroku/examples/demo/. ~/kiroku-demo/ && cd ~/kiroku-demo && git init
```

```bash
sh ~/kiroku/install.sh . && git add -A && git commit -m init
```

```bash
kiro-cli chat
```

Kiro に、次の文章を1つずつ順に送ります。

1. `python3 run_tests.py を実行して`（全テスト成功）
2. `tests/test_calc.py に、add(2, 2) が 5 になることを確かめるテストを追加して、python3 run_tests.py を実行して。失敗してもそのままでよい`（正常な Red）
3. `いま追加したテストの期待値を 4 に直して、python3 run_tests.py を実行して`（全テスト成功）
4. `calc.py の add を a - b に書き換えて、python3 run_tests.py を実行して。失敗してもそのままでよい`（回帰）
5. `シェルで ls /no/such/dir を実行して。失敗してもそのままでよい`（ツール失敗）

`/quit` で終了し、記録と集約を確かめます。

```bash
python3 -c "import json;[print(e['type'], e.get('exit_code'), e.get('path') or e.get('command')) for e in map(json.loads, open('.kiro/retro/events.jsonl'))]"
```

```bash
python3 .kiro/kiroku/aggregate.py --by all
```

期待する結果は、正常な Red が1回、回帰が2種類、ツール失敗が1種類です。
続けて、上の「振り返る」の手順でレポートが出ることを確かめます。

## うまくいかないとき

- **何も記録されない**：`/hooks` にフックが出ているか、`python3` が通っているかを確かめます。
  記録処理のエラーは `.kiro/retro/record-errors.log` に残ります。
- **テスト結果が空（`results` が null）**：イベントの `note` を見ます。
  `report_not_found` はレポートが出ていません。`report_stale` はレポートが前回から更新されていません。
- **分類が「その他」ばかり**：全テストが通った記録がまだ無いか、テスト結果が記録されていません。

## やめるとき

`.kiro/hooks/kiroku.json`、`.kiro/kiroku/`、`.kiro/skills/kiroku-retro/`、`.kiro/agents/kiroku-retro.json` を削除します。
記録を消すには `.kiro/retro/` を削除します。

## 開発

```bash
python3 -m unittest discover -s tests -v
```

検証の経緯は [verification.md](verification.md) にあります。
