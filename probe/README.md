# 段階1の検証キット

Kiro CLI（V3）の実際の挙動を、手元の PC で確かめるための一式です。
実際の開発に使っているリポジトリでは実行しないでください。空の作業用ディレクトリで行います。

所要時間は15分ほどです。必要なものは kiro-cli（3.0 以降）、git、python3 です。

## 準備

このリポジトリを取得し、作業用ディレクトリを作って検証用ファイルをコピーします。

```bash
git clone https://github.com/yuyumama/kiroku ~/kiroku
```

```bash
mkdir ~/kiroku-probe-work && cd ~/kiroku-probe-work && git init
```

```bash
cp -R ~/kiroku/probe/template/. ~/kiroku-probe-work/
```

```bash
kiro-cli --version
```

コピーされるのは `.kiro/` 配下の次のファイルです。

| ファイル | 役割 |
|---|---|
| `hooks/kiroku-probe-core.json` | SessionStart、SessionEnd、UserPromptSubmit、PreToolUse、PostToolUse、Stop のダンプ |
| `hooks/kiroku-probe-files.json` | PostFileCreate、PostFileSave、PostFileDelete のダンプ |
| `kiroku-probe/dump.py` | フックの入力を `kiroku-probe/out/` に保存する |
| `kiroku-probe/summarize.py` | 保存した入力を、貼り付けても安全な形に要約する |
| `skills/kiroku-probe/SKILL.md` | スラッシュコマンドで呼べるかの確認用 |
| `agents/kiroku-probe.json` | 書き込み先を制限できるかの確認用 |

## 確認A：フックの入力

作業用ディレクトリで、対話モードの Kiro を起動します。

```bash
cd ~/kiroku-probe-work && kiro-cli chat
```

起動したら `/hooks` を入力し、`kiroku-probe-` で始まるフックが9本読み込まれているかを見ます。
読み込まれていないもの、警告が出たものがあれば、その表示を控えてください。

次の文章を、1つずつ順に送ります。ツールの許可を求められたら許可してください。

1. `シェルで echo hello を実行して`
2. `シェルで ls /no/such/dir を実行して。失敗してもそのままでよい`
3. `sample.txt を新しく作り、1行目に first と書いて`
4. `sample.txt の first を second に書き換えて`
5. `sample.txt を削除して`

終わったら `/quit` で終了し、要約を出します。

```bash
python3 .kiro/kiroku-probe/summarize.py --sessions
```

## 確認B：skill をスラッシュコマンドで呼べるか

もう一度 `kiro-cli chat` を起動し、次を入力します。

```
/kiroku-probe abc
```

`KIROKU-PROBE-SKILL-LOADED` が返れば成立です。
入力途中の補完候補に `kiroku-probe` が出たかどうかも見てください。

## 確認C：書き込み先の制限

まず定義が正しいかを確かめます。

```bash
kiro-cli agent validate --path .kiro/agents/kiroku-probe.json
```

次に、そのエージェントで起動します。

```bash
kiro-cli chat --agent kiroku-probe
```

次の文章を順に送ります。

1. `allowed/a.txt を作り、ok と書いて`
2. `denied/b.txt を作り、ng と書いて`
3. `/kiroku-probe abc`

期待する結果は、1が成功し、2が確認なしで拒否されることです。
3は、専用エージェントからも skill を呼べるかの確認です。

## 報告してほしいもの

次の5つを貼り付けてください。

1. `kiro-cli --version` の出力
2. 確認Aの `/hooks` の表示（読み込まれなかったフックと警告）
3. `summarize.py --sessions` の出力全文
4. 確認Bの結果（返ってきた文、補完候補に出たか）
5. 確認Cの結果（`agent validate` の出力、1〜3がそれぞれどうなったか）

`summarize.py` の出力には、キー名、値の型、文字列の長さしか含まれません。
文字列の中身を出すのは `hook_event_name` と `tool_name` だけです。
`--sessions` はホームの `.kiro` 配下のファイル名を出すので、気になる名前があれば伏せてください。

`.kiro/kiroku-probe/out/` の生データは貼り付けないでください。

## うまくいかないとき

- **フックが1本も動かない**：`python3` が通っていない可能性があります。
  `python3 --version` を確かめ、Windows などで `python` しか無い場合は、フックの JSON 内の `python3` を `python` に置き換えてください。
- **`kiroku-probe-files.json` だけ読み込まれない**：ファイル系のトリガー名が手元の版と合っていません。
  そのまま報告してください。確認Aの残りは進められます。
- **エージェント定義がエラーになる**：エラー文をそのまま報告してください。こちらで定義を直します。

## 後片付け

```bash
rm -rf ~/kiroku-probe-work
```
