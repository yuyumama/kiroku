# 実機での確認の手順（ベストプラクティス集と、承認からの許可の提案）

この手順は、利用者の端末で Kiro CLI を動かせる AI が実行する。対象は、Issue #24 の設計（[best-practices-design.md](best-practices-design.md)）で実装した PR #25〜#28（kiroku の main `ec0a568` 以降）である。自動テストでは確かめられない、Kiro そのものの振る舞いと、振り返りのエージェントの振る舞いを確かめる。

結果は、最後に [verification.md](verification.md) に記録として足す。期待と違ったものは「不一致」として、根拠と一緒に記録する。kiroku のコードは直さない。

## 守ること

- **使い捨てのコピー**：作業は、新しく作る作業用のフォルダ（以下 `$WORK`）の中の、デモの使い捨てのコピーだけで行う。利用者のプロジェクトと、kiroku のリポジトリの作業ツリーには、手順8で結果を書くときだけ触る。
- **範囲**：承認画面で Always allow を選ぶときは、範囲を This workspace にする。All workspaces は、利用者のほかのプロジェクトにも効くので選ばない。
- **ネットワーク**：push は、`$WORK` の中の bare リポジトリに向ける。外のサービスには送らない。
- **拒否されたとき**：拒否された操作は、別の方法で回避しない。拒否されたこと自体を結果として記録する。
- **記録するもの**：ログからは、キーと値、短いコマンドだけを写す。会話の本文は、判定に要る一節だけにする。秘密値らしいものは伏せる。
- **`~/.kiro` の下**：読むだけにする。手順の中で Kiro が作ったもの（`~/.kiro/workspace-roots/<hash>/` など）は、手順8で一覧にして利用者に渡す。消すかどうかは利用者が決める。
- **Approve for me**：有効にしない（規則で決まらない操作を LLM に判定させる機能で、確かめたいのは人の承認の記録のため）。

## 判定の言葉

各ケースの結果は、[demo-cases.md](demo-cases.md) の「合格」「不一致」「前提不足」「未実施」で書く。期待する結果と反例は、demo-cases.md のケース一覧（D28〜D35）がただ1つの出どころである。この手順は、場面の作り方と、何を記録するかだけを書く。

## ログの値の読み方

承認の記録は、セッションのログ `~/.kiro/sessions/<hash>/<session_id>/messages.jsonl`（`KIRO_HOME` があればその下）にある。セッションの ID は、デモのコピーの中で次を実行すると一覧に出る。

```bash
python3 .kiro/skills/kiroku-retro/scripts/aggregate.py --since all --section sessions
```

承認画面に答えた記録と、対応する呼び出しは、次のスクリプトで並べる。`$WORK/show_interactions.py` として保存し、ログのパスを渡して実行する（デモのコピーやリポジトリの中には置かない）。

```python
import json, sys

calls, rows = {}, []
with open(sys.argv[1], encoding="utf-8") as handle:
    for line in handle:
        item = json.loads(line)
        payload = item.get("payload") or {}
        kind = payload.get("type")
        if kind == "tool_call":
            calls[payload.get("toolCallId")] = payload
        elif kind in ("pending_interaction", "interaction_resolved"):
            rows.append((item.get("timestamp"), payload))
for stamp, payload in rows:
    call = calls.get(payload.get("toolCallId"), {})
    fields = {k: v for k, v in payload.items() if k != "type"}
    print(stamp, payload["type"], json.dumps(fields, ensure_ascii=False), "|",
          call.get("toolName"), (call.get("args") or {}).get("command"))
```

## 0. 準備

1. `kiro-cli --version`、`python3 --version`、OS、kiroku のリポジトリの `git rev-parse --short HEAD` を記録する。
2. `$WORK` を作り、kiroku の `examples/demo` を2つコピーする。`$WORK/probe`（手順1・2の診断用）と `$WORK/demo`（手順3〜6の場面用）。それぞれで `git init`、最初のコミットを作る。2つに分けるのは、手順1で保存する許可の規則と承認の記録が、場面のほうの数に混ざらないようにするため。
3. 両方に kiroku を入れる：`sh <kiroku>/install.sh $WORK/probe`、`sh <kiroku>/install.sh $WORK/demo`。
4. 両方で `python3 .kiro/skills/kiroku-retro/scripts/doctor.py --command "python3 run_tests.py"` を実行する。
5. `$WORK/demo` に、D28 の CI の定義 `.github/workflows/ci.yml` を足してコミットする（中身は次のとおり。テストのコマンドはコメントの中だけにある）。git のリモートは、手順5まで足さない（D31 はリモートが無い状態で見る）。

```yaml
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo hello  # python3 run_tests.py は後で足す
```

完了：版の4項目が記録され、doctor が両方のコピーで終了コード0になっている。

## 1. 承認の記録の形（最優先）

`$WORK/probe` で、既定のエージェントの開発のセッションを開き、次の順に依頼する。承認画面が出たら、表の選択をする。各依頼の後に、ログを「ログの値の読み方」のスクリプトで読み、値を記録する。

| # | 依頼（エージェントに頼むコマンド） | 承認画面での選択 |
|---|---|---|
| 1 | `git status` を実行して | 承認画面が出るかを記録する。出たら Allow |
| 2 | `python3 run_tests.py` を実行して | Allow（1回） |
| 3 | `cat README.md` を実行して | Deny（画面が出なければ、そのことを記録する） |
| 4 | `ls -la` を実行して | 画面で Escape |
| 5 | `git diff`（引数なし）を実行して | 画面が出るかを記録する（手順2の対照）。出たら Deny |
| 6 | `git diff HEAD` を実行して | Always allow。段は2語（`git diff *`）、範囲は This workspace |

記録すること：

- 各行の `interaction_resolved` の `outcome`、`selectedOption`（キーが無ければ「キーなし」）、ほかのキーの名前。`pending_interaction` の有無。#1 で承認画面が出たか（既定の権限で、読み取りだけのコマンドに承認画面が出るか）。
- #6 で保存された規則のファイル。`~/.kiro/workspace-roots/*/permissions.yaml` のうち、#6 の直後に更新されたものを探し、パスと中身（規則の部分）を写す。その `<hash>` が、このセッションのログの置き場 `~/.kiro/sessions/<hash>/` のフォルダ名と同じかを記録する。
- ほかの範囲（ユーザー共通の `~/.kiro/settings/permissions.yaml`）が変わっていないこと。

- 答えの `toolCallId` と、対応する呼び出しの `toolCallId` が同じか。違うときは、呼び出しのどのキーに答えの `modelToolUseId` の値があるか（キーの名前だけ）。

完了：#1〜#6 の行がすべて埋まった表と、規則のファイルのパス・中身・`<hash>` の一致、`toolCallId` の対応が記録されている。

## 2. 規則の当たり方

手順1の #6 で保存した `git diff *` が、どのコマンドに当たるかを確かめる。#1 の引数なしの `git diff` は、手順1の #5 の結果（保存前）と比べる。同じ `$WORK/probe` のセッション（または新しいセッション）で、次を1つずつ依頼し、承認画面が出たかを記録する。出たら Deny を選ぶ（規則を増やさないため）。

| # | 依頼するコマンド | kiroku の近似での予想 |
|---|---|---|
| 1 | `git diff` | 当たらない（画面が出る）。kiroku は、引数の無い形を別の完全一致として規則に入れている |
| 2 | `git diff --stat` | 当たる（画面が出ない） |
| 3 | `git -C . diff` | 当たらない（画面が出る） |
| 4 | `git diff --stat && git status` | `git diff` の部分は当たり、`git status` の部分は別に判定される |

完了：#1〜#4 の「画面が出たか」と、予想との一致・不一致が記録されている。不一致があれば、kiroku の規則の案（`approvals.rule_for`）と、`approvals._glob` の近似が実機と違うことを、不一致として書く。

## 3. 開発の会話（1回目の場面）

`$WORK/demo` で、既定のエージェントの開発のセッションを開き、[demo-cases.md](demo-cases.md) の「実機での会話」のセッションA、セッションB、セッションC（D30）を、それぞれ別のセッションで行う。承認画面が出たら Allow を選ぶ。

完了：3つのセッションの ID と、各セッションで与えた発話が記録されている。

## 4. 振り返り1回目（D28、D30、D31。D32 の準備）

1. `$WORK/demo` で `kiro-cli --agent kiroku-retro` を起動し、`/kiroku-retro` と入力する。
2. エージェントが実行したコマンドに `--since last` があるかを記録する。
3. 質問が出たら、答える前に記録する：冒頭の「上から重要な順です。全部に答えなくて構いません」の有無、質問の数（件数を絞っていないか）、並び、各質問の手がかり、質問の後にエージェントがターンを終えて待ったか。
4. 次のように答える。
   - 手作業の確認（月の集計の合計）の質問：「a。通常分は合計が一致すればよい。返金だけは人が見る」
   - コミットの前の検査（`practice:BP-S4`）の質問：「a」（使いたい）。案を作らせるため（D32 の準備）。
   - ほかの質問：見立てのとおりなら「はい」、分からなければ「わからない」。
5. レポートができたら、`python3 .kiro/skills/kiroku-retro/scripts/aggregate.py --check-report <レポート>` を自分でも実行し、終了コードを記録する。
6. エージェントが採否を聞いたら、BP-S4（pre-commit）の案は「見送り」と答える。ほかの案は「判断しない」。`python3 .kiro/skills/kiroku-retro/scripts/ledger.py list` で、BP-S4 の案が見送りとして記録されたことを確かめる。
7. D28、D30、D31 を、demo-cases.md の期待と反例で判定する。D28 は集約の `D5` の行（「一致する行が無い」）とレポートの記述、D30 はノートの `manual_check`、集約の「手作業の確認」、質問、案（V3）、D31 は集約の BP-S5（「当てはまらない」）と、CI を勧める質問・案が無いこと。

完了：レポートのパス、検査の終了コード、質問の観察、D28・D30・D31 の判定（根拠つき）、台帳の BP-S4 の見送りが記録されている。BP-S4 の質問が出なかったときは、D32 を「前提不足」として記録し、先に進む。

## 5. 承認の場面（D33〜D35）

1. `$WORK` に bare リポジトリを作り（`git init --bare $WORK/remote.git`）、`$WORK/demo` で `git remote add origin $WORK/remote.git` を実行する。
2. [demo-cases.md](demo-cases.md) の「承認の場面」のセッションD1、D2、D3 を、それぞれ別のセッションで行う。承認画面の選択は、そこに書かれたとおりにする（Always allow は選ばない）。
3. 承認画面が出なかった依頼は、そのことを記録する。利用者がすでに持っている規則で許可されている可能性がある。その依頼に関わるケースは、手順6で「前提不足」にする。

完了：3つのセッションの ID と、各依頼での承認画面の選択（出なかったものは「出なかった」）が記録されている。

## 6. 振り返り2回目（D29、D32、D33〜D35）

1. 振り返りの前に、`$WORK/demo` で `python3 .kiro/skills/kiroku-retro/scripts/aggregate.py --since last` を実行する。出力の「期間」の始まりが、1回目のレポートの検証値の `until` と同じか（1回目のレポートに未読が残っていたなら `since` と同じか）、「未読のセッション」に1回目で読んだセッションが入っていないかを記録する（D29）。
2. `kiro-cli --agent kiroku-retro` で `/kiroku-retro` を実行する。
3. 質問を記録する（手順4の3と同じ項目）。BP-S4（pre-commit）を聞かないこと（D32）、承認の質問（「毎回確かめたいですか。それとも、自動で通してよいですか？」）が `approval:python3 run_tests.py` について出ること（D33）、`ls`・`git push`・`git commit` について許可の質問が出ないこと（D34、D35）。
4. 承認の質問には「a」（自動で通してよい）と答える。ほかは手順4の4と同じ。
5. レポートの承認の案を記録する：対象の型（`approval:python3 run_tests.py`）、規則の案（完全一致 `python3 run_tests.py`）、当たる例と当たらない例、Always allow の段と範囲の書き方、対象のファイル（ワークスペースの規則のファイル）、エージェント定義に書く形、「未検証」の明記。
6. `--check-report` を自分でも実行し、終了コードを記録する。
7. D29、D32、D33、D34、D35 を判定する。D34・D35 は、集約の「承認の記録」の「候補にしない承認」の行（理由つき）も根拠にする。集約の「答えと呼び出しの結び方」「呼び出しに結べなかった答え」「知らない答えの値」の行を写す。

完了：手順1の期間と未読の観察、質問の観察、承認の案の中身、検査の終了コード、5つのケースの判定が記録されている。

## 7. 更新の互換

1. `$WORK/update` に、デモの3つ目のコピーを作り、`git init` と最初のコミットをする。kiroku のリポジトリの `7ae4802`（このシリーズの前の main。骨組みの版5）を、`mkdir $WORK/kiroku-old && git -C <kiroku> archive 7ae4802 | tar -x -C $WORK/kiroku-old` で取り出し（リポジトリは変えない）、`sh $WORK/kiroku-old/install.sh $WORK/update` で入れる。
2. `$WORK/update` で開発のセッションを開き、「README の方法でテストを実行して」と依頼する（承認画面は Allow）。`.kiro/kiroku/data/events.jsonl` の行数を記録する。
3. `kiro-cli --agent kiroku-retro` で `/kiroku-retro` を実行し、質問にはすべて「後で」と答え、レポートを書き終えさせる。`--check-report` の終了コードを記録する（版5のレポート）。
4. 手順2の開発のセッションを開いたまま、別の端末で、今の kiroku（main）から `sh <kiroku>/install.sh $WORK/update --update` を実行する。インストーラーの出力をそのまま記録する。`one_step_max = null（上限なし）`、`question_max = null（上限なし）`、`approval_min_sessions = 2` が、既定値で動く項目として出るかを見る。
5. 開いたままのセッションで、もう一度テストを実行させる。`events.jsonl` に行が増えたか、`.kiro/kiroku/data/record-errors.log` が増えていないかを記録する（更新の途中で記録が欠けないか）。
6. 手順3の版5のレポートに、今の kiroku で `--check-report` を実行し、終了コードを記録する。
7. もう一度 `/kiroku-retro` を実行し、版8のレポート（`<!-- kiroku-report:8 -->`）ができて、検査が通るかを記録する。

完了：インストーラーの出力、更新の前後の `events.jsonl` の行数と `record-errors.log` の行数、版5のレポートの更新後の検査の終了コード、版8のレポートの検査の終了コードが記録されている。

## 8. 結果を書く

1. kiroku のリポジトリの [verification.md](verification.md) の末尾に、日付つきの記録を足す。書くこと：Kiro の版、OS、kiroku の版。手順1・2の表。D28〜D35 の判定（根拠つき）。手順7の結果。確かめたことと、確かめていないことを分けて書く。不一致は、そのまま不一致として書く。
2. 手順1の結果で、次の文書の「未検証」の記載が変わるものを、一覧にして利用者に示す（文書の直しは、利用者が決めてから行う）：[README.md](README.md) の「毎回の承認から、許可の規則を提案する」、[best-practices-design.md](best-practices-design.md) の13節、`references/harness-guide.md` の案の書き方。
3. 作ったものの一覧を利用者に渡す：`$WORK` の場所、`~/.kiro/workspace-roots/` の下に増えた `<hash>` のフォルダ、`~/.kiro/sessions/` の下に増えたセッション。

完了：verification.md に記録が足され、文書の直しの候補と、作ったものの一覧が利用者に渡っている。
