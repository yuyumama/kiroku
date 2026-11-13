# kiroku の使い方

Kiro での開発中の記録と現在のハーネスを比較し、変更差分付きの改善案を作ります。
目的は、同じ指摘・説明の繰り返しとハーネス整備の負担を減らすことです。不具合・作業失敗も分析します。

現在は評価段階です。週次を想定して手動で専用エージェントを呼びます。
自動起動・自動採否・適用・過去の提案管理は今後の対象です。
実装の対応範囲と製品上の検証状況は [互換性調査](compatibility.md) と [評価手順](evaluation.md) を参照してください。

## 必要なもの

- Kiro CLI V3 または Kiro IDE 1.x（旧 IDE 0.x は対象外）
- Python 3.9 以降、Git。Python の追加パッケージは不要
- Windows の導入は PowerShell、Linux/macOS は POSIX shell

公式の Kiro 導入手順は [Installation](https://kiro.dev/docs/getting-started/installation/) にあります。
CLI・IDE の認証は利用するアカウントで行います。

## 導入と更新

Linux/macOS:

```bash
sh /path/to/kiroku/install.sh /path/to/project
sh /path/to/kiroku/install.sh /path/to/project --update
```

PowerShell:

```powershell
& C:\tools\kiroku\install.ps1 -Target 'C:\work\my project'
& C:\tools\kiroku\install.ps1 -Target 'C:\work\my project' -Update
```

既定の Python 起動名は POSIX が `python3`、PowerShell が `python` です。
Windows Python Launcher を使う場合は `-PythonCommand 'py -3'` を指定します。
選択した起動名を、フック・専用エージェントのシェル許可規則・スキルの実行例に反映します。
`install.py` は両インストーラーの共通処理です。

既存の同名ファイルが異なる場合、通常導入は変更前に止まります。
`--update`／`-Update` では配布ファイルを更新しますが、既存の `config.json` は保持します。
新しい設定を使う場合は既存設定へ追加してください。旧 `prompt_max_chars` は現在使いません。

```text
.kiro/
├── hooks/kiroku.json
├── agents/kiroku-retro.json
├── skills/kiroku-retro/
│   ├── SKILL.md
│   ├── references/    harness-guide.md（案の形の選び方）
│   └── scripts/       record.py、aggregate.py、excerpt.py、prompts.py、harness.py、notes.py、ledger.py、review.py
└── kiroku/
    ├── config.json
    └── data/          events.jsonl、ledger.jsonl、notes/、reports/ など
```

`data/` はインストーラーが `.gitignore` に追加します。コード・出力・秘密値が含まれうるため、コミットや PR に含めません。
更新前に記録したユーザー発話本文は自動削除しませんが、集約には利用しません。

導入後、CLI は `/hooks`、IDE は Hooks の画面で3本の記録フックの読み込みを確認します。
CLI と IDE には共通の v1 フック形式を使います。製品上の発火・入力形式は評価手順で確認してください。

## 記録するもの

| イベント | 内容 |
|---|---|
| `tool_failure` | シェル失敗のコマンド、終了コード、出力末尾 |
| `tool_cancelled` / `tool_denied` | 人による取り消し／権限規則による拒否 |
| `write` | ファイルのパス、内容ハッシュ、コミット |
| `prompt` | 発話の時刻と session ID。本文は保存しない |
| `test_run` | 設定された JUnit レポートと、その時点の Git 状態 |
| `recording_gap` | 未知のシェル応答など、判定できなかった実行 |

通常の成功したシェル実行と kiroku 自身のコマンドは記録しません。
`execute_bash` と `execute_pwsh`、書き込みは `fs_write`・`fs_append`・`str_replace` とファイル保存フックに対応します。
シェル応答は既存の `Exit Code:` 形式を解析します。未知の応答を成功扱いにはしません。

コマンド・出力は秘密値らしい文字列をマスクしますが完全ではありません。
記録処理の異常で本来の開発を止めず、`data/record-errors.log` にエラーを残します。
同時フックによる記録・JUnit 読込状態の競合は、OS のファイルロックで直列化します。

## テスト結果を取り込む

導入先の `.kiro/kiroku/config.json` に設定します。

```json
{
  "tests": [
    {"match": "\\bpytest\\b", "report": "reports/junit.xml", "format": "junit"}
  ],
  "sessions_dir": null
}
```

`match` はテストコマンドの正規表現、`report` はプロジェクトルートからの相対パスです。glob が使えます。
テスト側で毎回 JUnit XML を出す設定が必要です。設定がない場合はテスト結果の取り込みを行いません。

分類は記録時ではなく集約時に行います。

| 分類 | 根拠 |
|---|---|
| Red 候補 | 全テスト成功後に新しく観測されたテスト、またはテストファイルの変更 |
| 回帰候補 | 過去に通っていたテストで、テストファイルの変更を確認できない |
| その他 | 基準になる成功記録や Git 状態などが不足 |

これは原因や意図の確定ではありません。テスト追加、部分実行、環境要因、flaky test などを会話・差分と照らします。
ファイルの内容差分も変更者を証明しないため、「人間の手直し候補」は確認材料として扱います。

## 会話はログから二段階で読む

会話本文を kiroku に複製保存しません。

1. セッションを1件ずつ要約表示で読み、改善候補を見つけます。
2. 候補の前後だけ、AI 応答・ツール実行を読みます。

ログの探索対象は、そのプロジェクトのイベントに登場する session ID と、ログの `session.json` の作業フォルダがこのリポジトリ（またはその配下）を指すセッションです。
後者により、フックが1度も動かなかった会話（質問だけのセッションなど）も一覧に出ます。一覧の「記録」の列が「ログのみ」になります。
CLI の既定探索先は `~/.kiro/sessions/*/<session_id>/messages.jsonl`（`KIRO_HOME` 指定時はその配下）。
ログの内容形式は公開された安定 API ではありません。ログがない・読めない・一部壊れている場合は不足を表示し、会話に基づく判断を保留します。
サブエージェントのログは自動収集しません。

IDE の内部保存場所は決め打ちしません。Export Chat で保存した ZIP を、例えば git 対象外の `data/exports/` に展開し、設定します。

```json
{"sessions_dir": ".kiro/kiroku/data/exports"}
```

対応する配置は `<sessions_dir>/<session_id>/messages.jsonl`、その間に1階層ある配置、または `sessions_dir` 自体がセッション ID のディレクトリである配置です。
絶対パスも指定できます。ZIP は自動展開しません。本文形式の実機互換性は別途確認が必要です。

発話を全部読むと量が多すぎるため、次の3つで量を抑えます。

- **セッション一覧**：集約結果に、セッションごとの発話数と、振り返りノートの有無が出ます。
- **1件ずつの要約表示**：ユーザー発話は原文（長ければ頭と末尾）、その後のツール実行は件数、応答は最後の1件の冒頭だけを出します。
- **振り返りノート**：読み終えたセッションの要点を `data/notes/<session_id>.json` に残します。次回はノートを使い、ログを読み直しません。

```bash
python3 .kiro/skills/kiroku-retro/scripts/aggregate.py --since 7d
```

```bash
python3 .kiro/skills/kiroku-retro/scripts/aggregate.py --since 7d --session <session_id>
```

1回の振り返りで読む量は、件数ではなく文字数の見積もりで区切ります。集約の「今回読むセッション」に、未読のものが新しい順に、上限に収まるところまで並びます。
短いセッションなら数十件、長いセッションなら数件になります。上限は `config.json` の `retro_read_chars`（既定 50,000 文字）で変えられます。

未読が残った場合は、集約とレポートに件数が出ます。もう一度 `/kiroku-retro` を実行すると、続きから読みます。ノートがあるセッションは読み直さないので、何度実行しても重複しません。
全発話を全文で読みたいときは、予備として `--prompts` と `--prompt-offset` によるページ表示が使えます。

```bash
python3 .kiro/skills/kiroku-retro/scripts/excerpt.py --session <session_id> --index <index> --context 3
```

`index` は要約表示に出ます。実行イベントから探す場合は `--around <時刻>` や `--command "<一部>"` を使います。
長い項目には続きの `--offset` を表示します。中身を保存せず、必要な範囲を出力します。
旧 `--conversation` は廃止しました。

## 振り返りを実行する

CLI は作業セッションと別に、専用エージェントを起動します。

```bash
kiro-cli chat --agent kiroku-retro
```

IDE は専用エージェント `kiroku-retro` を選び、新しいチャットで同じスキルを呼びます。

```text
/kiroku-retro
```

既定は直近7日。期間は後ろに指定できます。
PowerShell の実行例は、導入時に選んだ `python` または `py -3` に読み替えてください。

比較するのはプロジェクト内とユーザー共通の steering・hooks・skills・agents・MCP 設定、および関連する開発設定です。
原則としてプロジェクト内の変更を提案し、共通設定の変更は他プロジェクトへの影響とともに別提案にします。
記録がない場合も、README・コード・開発設定から必要な初期ハーネスを検討します。

レポートは `data/reports/YYYY-MM-DD-HHMM.md` に保存します。
改善案は文章で書きます。既存設定との対応、変更の内容、その形を選んだ理由、期待する効果と確かめ方を含めます。
差分は作らず、ハーネスも変更しません。適用するかどうかは、レポートを読んだ人が決めます。必要性がなければ提案は「なし」です。

会話からの型は、原則として2つ以上の別のセッションに根拠があるものだけを挙げます。
案の形の選び方と、既存の決まりが効かなかった理由の切り分けは、skill の `references/harness-guide.md` にまとめています。Kiro の公式ドキュメントの推奨に基づきます。

専用エージェントの書き込みはレポートと振り返りノートの配下、シェルは集約・抜粋・台帳のスクリプトに制限します。
この評価版で人が品質を確認する運用は、将来の自動化を制限するものではありません。

## 根拠を機械的に確かめる

振り返りのエージェントの申告を、そのままは信じません。次の2つはスクリプトが確かめます。

- **引用の照合**：ノートの引用が、そのセッションのユーザー発話に実在するかを確かめます。見つからない引用は数えず、集約の「ノートの検査で外したもの」に出します。
- **セッション数の集計**：同じトピックが、いくつの別のセッションに出たかを数えます。2つ以上のものだけが「会話からの型の候補」になります。継続的な方針は1回でも候補になります。

一度照合できた引用は `data/notes-verified.json` に残すので、あとで Kiro がログを消しても数えられます。

## 採用・見送りを記録し、効果を追う

レポートの案には `P1`、`P2` と番号が付き、それぞれに「対象の型」が書かれます。

**ふつうは、振り返りの会話の中で答えるだけです。** レポートを書き終えたエージェントが、どれを採用し、どれを見送るかを尋ねます。

```text
P1 は採用、P2 は見送り。今は不要だから
```

エージェントは、あなたが明言した判断だけを記録します。答えなかった案は記録されず、あとで決められます。
あとから決めるときは、振り返りのエージェントを起動して「前回の P1 を採用した」と伝えます。

ハーネスへの反映そのものは、これまでどおり手で行います。記録するのは「採用したかどうか」だけです。

コマンドで直接記録することもできます。

```bash
python3 .kiro/skills/kiroku-retro/scripts/ledger.py list
```

```bash
python3 .kiro/skills/kiroku-retro/scripts/ledger.py adopt P1
```

```bash
python3 .kiro/skills/kiroku-retro/scripts/ledger.py reject P2 --reason "今は不要"
```

番号だけを書くと、最新のレポートの案を指します。古いレポートの案は `2026-11-13-0856#P1` のように書きます。

次回以降の集約に、次の2つが出ます。

| 節 | 内容 |
|---|---|
| 採用した案の追跡 | 対象の型が、採用の前後で何回出たか。「再発なし」「減った」「減っていない」「まだ日が浅い」 |
| 見送った案 | 見送りの後に、対象の型が何回出たか。振り返りは、新しい発生が無ければ同じ案を出しません |

採用後3日未満は「まだ日が浅い」として判定しません。採用前の期間は、採用後と同じ長さ（短くても7日）を取ります。

## steering の重さ

集約に、steering と `AGENTS.md` のファイルごとの文字数、読み込みの条件、常時読み込みの合計が出ます。
常時読み込みに書き足す案には、合計が何文字増えるかが添えられます。
合計が目安（8,000 文字。最初の値です）を超えていると、足す前に分割や条件付きへの移動を検討します。

## 使い方を見直すときの要約

しばらく使ったあとで、どの信号が役に立ったか、振り返りの質は十分かを見直すための要約です。
数だけを出し、コード、コマンド、発話、ファイルのパス、トピックの名前は出しません。リポジトリの中身を見せずに、状況を共有できます。

```bash
python3 .kiro/skills/kiroku-retro/scripts/review.py --since 14d
```

出るのは、週ごとの記録の数、信号ごとの種類の数と繰り返しの度合い、セッションとログの状態、会話からの型の数、案の採用・見送りの数、採用した案のその後、steering の量です。

## アンインストール

kiroku を導入先から取り除きます。何を残すかを聞かれるので、番号で選びます。

Linux/macOS:

```bash
sh /path/to/kiroku/uninstall.sh /path/to/project
```

PowerShell:

```powershell
& C:\tools\kiroku\uninstall.ps1 -Target 'C:\work\my project'
```

| 選択 | 消すもの | 残すもの |
|---|---|---|
| 1) すべて消す | フック、専用エージェント、skill、設定、記録、振り返りノート、台帳、レポート、`.gitignore` の kiroku の行 | なし |
| 2) レポートだけ残す | 上のうち、レポート以外 | `.kiro/kiroku/data/reports/` と、`.gitignore` の行 |
| 3) やめる | なし | すべて |

選んだあとに、消すものと残すものの一覧が表示され、確認を求められます。`y` と答えるまで、何も消しません。

**消すのは kiroku が置いたものだけです。** steering や、ほかの hooks・skills・agents には触りません。
kiroku の改善案をもとに手で直したハーネスは、そのまま残ります。

レポートを残す場合は、`.gitignore` の行も残します。レポートには会話の引用が含まれうるので、コミットされないようにするためです。

選択をあらかじめ指定することもできます。

| オプション（PowerShell） | 意味 |
|---|---|
| `--all`（`-All`） | すべて消す |
| `--keep-reports`（`-KeepReports`） | レポートだけ残す |
| `--dry-run`（`-DryRun`） | 消すものを表示するだけで、消さない |
| `--yes`（`-Yes`） | 確認を省く |

```bash
sh /path/to/kiroku/uninstall.sh /path/to/project --keep-reports --dry-run
```

導入先のリポジトリに kiroku のファイルをコミットしていた場合は、取り除いたあとに `git status` に削除として出ます。内容を確かめてコミットしてください。

## 開発・評価

```bash
python3 -m unittest discover -s tests -v
```

テストは一時 Git リポジトリと模擬フック・ログを使います。製品連携や LLM の提案品質を証明するものではありません。
サンプルは `examples/demo/`、製品での確認手順は [evaluation.md](evaluation.md) にあります。
過去の CLI 実測は [verification.md](verification.md) に記録されています。
