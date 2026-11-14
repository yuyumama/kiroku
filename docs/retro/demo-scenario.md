# サンプルで、本番に近い振り返りを試す

`examples/demo/` は、ハーネスを備えた小さなプロジェクト（家計簿の CLI）です。
kiroku を入れて数回やり取りし、振り返りが既存のハーネスと照らして案を出せるかを確かめるために使います。

このファイルには、サンプルに仕込んである不備と、振り返りに期待する結果を書いています。
振り返りのエージェントに答えを見せないよう、サンプルの中ではなく、ここに置いています。

範囲を広げて検査する場合は [拡張ケースと判定基準](demo-cases.md) を使います。実行できたこと、集計が合うこと、提案の意味が妥当なことを別々に判定します。

## サンプルの中身

テスト結果の記録まで検証する回では、`python3 run_tests.py` を実行するよう依頼します。`make test` の失敗はハーネスの不整合を見つける材料ですが、代わりにJUnitを生成しない `python3 -m unittest` を使うだけでは、kirokuのテスト分類を確認したことにはなりません。

導入後、`doctor.py --command "python3 run_tests.py"` で設定を診断し、Kiroの `/hooks` で実行前フックを含む4本を確認します。振り返りは `kiro-cli --agent kiroku-retro` で別セッションを起動します。レポート保存後はskillの `--check-report` が成功したことと、本文の根拠を確認します。

Python の標準ライブラリだけで動きます。追加のパッケージは要りません。

| 種類 | ファイル | 役割 |
|---|---|---|
| コード | `kakeibo/`、`tests/`、`run_tests.py` | 家計簿の CLI と、テスト（11件）。結果を `reports/junit.xml` に出す |
| steering | `.kiro/steering/product.md` | プロジェクトの前提と決まり。常時読み込み |
| steering | `.kiro/steering/testing.md` | テストの決まり。常時読み込み |
| steering | `.kiro/steering/python-style.md` | Python の書き方。ファイルの種類で読み込み |
| steering | `.kiro/steering/release.md` | リリースの手順。常時読み込み |
| hook | `.kiro/hooks/project.json` | 保存した Python の構文検査と、危険なシェルコマンドの停止 |
| skill | `.kiro/skills/add-category/` | カテゴリを追加する手順 |
| agent | `.kiro/agents/reviewer.json` | 読むだけのレビュー担当 |
| kiroku の設定 | `.kiro/kiroku/config.json` | `run_tests.py` をテスト実行として記録する |

## 準備

```bash
mkdir ~/kiroku-demo && cp -R ~/kiroku/examples/demo/. ~/kiroku-demo/ && cd ~/kiroku-demo && git init
```

```bash
sh ~/kiroku/install.sh . && git add -A && git commit -m init
```

以前の `~/kiroku-demo` が残っている場合は、先に別名に変えるか削除します。
Kiro を起動して `/hooks` を入力し、`kiroku-record-` の4本と、`check-python-syntax`、`guard-shell` が出ることを確かめます。

## 会話の例

セッションを分けて行います。1つ終わるごとに `/quit` で終了し、`kiro-cli chat` を起動し直します。
文言はそのままでなくて構いません。**太字の発話は、2つ以上のセッションで言う**と、繰り返しの型として拾われます。

### セッション1

1. `テストを実行して`
2. `支出に「医療」のカテゴリを足して`
3. `月の集計に、前の月との差額を出して。増えたら +1,200円、減ったら -300円 の形で`
4. **`説明が長い。結論から書いて`**
5. **`コミットして。コミットメッセージは日本語で書いて`**

### セッション2

1. `テストを実行して`
2. `カテゴリごとの年間の合計を出す year コマンドを足して`
3. **`説明が長い。結論から書いて`**
4. **`コミットして。コミットメッセージは日本語で。前にも言ったよ`**

### セッション3（レビュー担当）

```bash
kiro-cli chat --agent reviewer
```

1. `直近のコミットをレビューして`
2. `型ヒントや文字列の書式の決まりは見た？ steering に書いてあるよ`

### 振り返り

```bash
kiro-cli chat --agent kiroku-retro
```

`/kiroku-retro` を入力します。

## 仕込んである不備と、期待する結果

| # | 不備 | 場所 | 期待する振り返り |
|---|---|---|---|
| 1 | 実行できない決まり。`make test` と書いてあるが、Makefile が無い。README は `python3 run_tests.py` | `steering/testing.md` | `make test` の失敗が記録に出れば、ツール失敗の型として挙がる。新しい決まりを足すのではなく、既存の文面を直す案になる |
| 2 | 読み込まれない決まり。対象が `src/**/*.py` だが、コードは `kakeibo/` にある | `steering/python-style.md` | 型ヒントや書式の指摘が出たとき、「決まりはあるが読み込まれていない」と切り分け、読み込みの条件を直す案になる |
| 3 | エージェントの `resources` が無い | `agents/reviewer.json` | 現行CLIは既定で決まりを継承するため、未記載だけを欠陥としない。継承の無効化や読み込み条件の不一致を確認できた場合だけ案を出す |
| 4 | 書かれていない決まり。コミットメッセージの言語、説明の仕方 | どこにも無い | 2つのセッションで繰り返した発話が、会話からの型の候補になる。steering に書く案になる |
| 5 | 常時読み込みにする必要がない資料。リリースの手順 | `steering/release.md` | 「steering の重さ」に出る。足す案を出すときに、手順を手動の読み込みに移す案が添えられる可能性がある |

不備は、実際のプロジェクトで起きやすいものを選んでいます。
1〜3は、手引き（`references/harness-guide.md`）の「既存の決まりが効かなかった理由を切り分ける」に対応します。

## 結果の見方

- 5つすべてが出るとは限りません。出なかったものは、記録に信号が無かったのか、振り返りが見落としたのかを分けて見ます。
  たとえば1は、エージェントが `make test` を試さず、最初から `python3 run_tests.py` を実行すれば、記録に出ません。
- 型の候補は、集約で確かめられます。

```bash
python3 .kiro/skills/kiroku-retro/scripts/aggregate.py --since 7d --section review
```

- 期待と違う結果は、不備として教えてください。どの不備に対して、レポートに何が書かれたか（または書かれなかったか）が分かると、手順書を直せます。

## 確かめていないこと

- サンプルのフック（構文検査、危険なコマンドの停止）は、スクリプトを直接動かして確かめました。Kiro から呼ばれたときの動作は未検証です。
- `guard-shell` のフックがコマンドを止めたとき、kiroku の記録にどう残るかは未検証です。
- 上の会話で、エージェントが期待どおりに動くとは限りません。会話の例は目安です。
