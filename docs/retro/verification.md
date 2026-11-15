# 段階1：検証結果

2026-11-14のデモ調査後の修正・検証と、CLI 3.3.0での実機確認は末尾に追記しています。以下の段階1の結論は2026-11-12時点の記録です。

ここで参照する初期検証キット `probe/` は、デモ拡充後に廃止しました。当時の確認方法として記述を残しています。現在の再確認は [デモの実機診断手順](demo-cases.md#デモを実機診断にも使う) を使います。

- 検証日：2026-11-12
- 対象：kiro-cli 3.2.1（V3 ハーネス、対話モード）
- 方法：公式ドキュメント（kiro.dev/docs）の確認と、`probe/` の検証キットによる実機確認

## 結論

崩れた前提はありません。段階2に進めます。
未確認として残るのは、headless での書き込み制限（段階2では使わない）と、導入先ごとのテスト実行経路（設定で吸収する）です。

| 項目 | 判定 |
|---|---|
| 1. セッションログの場所と形式 | 確認済み |
| 2. フックに渡る入力 | 確認済み |
| 3. 書き込み先の制限 | エージェント定義で確認済み。headless では未確認（使わない方針） |
| 4. skill をスラッシュコマンドで呼べるか | 確認済み |
| 5. テストフレームワークと実行経路 | 未確認（導入先ごとに異なるため。設定で吸収する） |

## 1. セッションログの場所と形式

**判定：確認済み。時刻とツール呼び出しの両方から該当箇所を特定できる。**

確認方法は、`python3 .kiro/kiroku-probe/summarize.py --sessions` と `python3 .kiro/kiroku-probe/followup.py` の出力です。

- セッションは `~/.kiro/sessions/<16桁の16進数>/<セッションごとのディレクトリ>/` にあります。
  中身は JSON 1個と JSONL 1個です（JSONL は最大で約2MBのものがありました）。
- 16桁の16進数は `~/.kiro/workspace-roots/` 配下のディレクトリ名と一致します。
  作業ディレクトリごとの識別子と推測しますが、対応の取り方は未確認です。
- `~/.kiro/sessions/cli/` もありますが、JSONL は2個とも0バイトでした。
  「`~/.kiro/sessions/cli/` の JSONL」という当初の情報は、V3 には当てはまりません。
- `~/.kiro/sessions/dashboard-search.db` は SQLite ですが、検索用の索引です（`marks` 表のみ読めました）。
  公式ドキュメントの「SQLite に保存」という記述は、実態と合っていません。
- セッションごとのディレクトリ名は、フックに渡る `session_id` と同じです。
  中身は `session.json` と `messages.jsonl` です。
- `messages.jsonl` の各行は `{"id", "timestamp", "payload"}` の形で、`payload.type` が種別です。
  確認できた種別は `user`、`assistant`、`tool_call`、`tool_result`、`turn_start`、`agent_note`、`ContextualHookInvoked`、`session_metadata` です。
- `tool_call` には `toolName`、`args`（シェルなら `args.command`）、`toolCallId` があります。
  `tool_result` には `content`、`success`、`toolCallId` があり、`toolCallId` で対応づけられます。
- `timestamp` の書式（ISO 形式か数値か）は、値を見ていないため未確認です。
  `excerpt.py` はどちらでも読めるようにし、コマンド文字列での検索を主にしています。

設計への影響：`session_id` から該当のログを開き、コマンド文字列または時刻で前後を抜き出せます（`excerpt.py`）。
ただしこの形式は公式に文書化されていないため、振り返りは events.jsonl だけでも成立するようにしています。

## 2. フックに渡る入力

**判定：確認済み。**

確認方法は、`probe/template/.kiro/hooks/` のダミーフック9本による実機ダンプです。
`/hooks` で9本すべての読み込みを確認し、`summarize.py` で入力の形を要約しました。

すべてのフックで、入力は STDIN の JSON でした。共通のキーは次の3つです。

- `hook_event_name`
- `cwd`
- `session_id`（41文字。環境変数 `KIRO_SESSION_ID` も同じ長さ）

フックごとの追加キーは次のとおりです。

| フック | 追加キー |
|---|---|
| SessionStart | なし |
| UserPromptSubmit | `prompt` |
| PreToolUse | `tool_name`、`tool_input` |
| PostToolUse | `tool_name`、`tool_input`、`tool_response` |
| PostFileCreate、PostFileSave、PostFileDelete | `file_path` |
| Stop | なし |
| SessionEnd | `reason` |

ツールごとの `tool_name` と `tool_input` は次のとおりです。

| 操作 | `tool_name` | `tool_input` のキー |
|---|---|---|
| シェル実行 | `execute_bash` | `command`、`cwd`、`description`、`run_in_background`、`timeout` |
| ファイル作成 | `fs_write` | `path`、`text` |
| ファイル編集 | `str_replace` | `path`、`oldStr`、`newStr`、`replace_all` |
| ファイル削除 | `delete_file` | `targetFile`、`explanation` |

依頼時に挙げた取得項目の結果です。

- **コマンド文字列**：取れます（`tool_input.command`）。
- **出力**：取れます。ただし `tool_response` は構造化されておらず、1個の文字列です。
- **終了コード**：取れます。独立したキーは無く、`tool_response` の末尾に書かれます。
  形は `Output:` の行、出力、空行、`Exit Code: <数値>` の行です。
  `echo hello` は `Exit Code: 0`、`ls /no/such/dir` は `Exit Code: 2` でした。
  ツールの承認を人が取り消した場合は `Exit Code: -1` で、出力に取り消された旨が入ります。
- **書き込んだファイルのパス**：取れます。ファイル系フックの `file_path` が最も素直です。
- **session_id**：取れます。

そのほかに分かった挙動です。

- 発火順は、PreToolUse、PostFileCreate、PostFileSave、PostToolUse の順でした。
  新規作成では PostFileCreate と PostFileSave の両方が発火します。
- UserPromptSubmit と Stop は、発話5回に対して3回ずつしか記録されませんでした。
  原因は未確認です。この2つを区切りとして当てにしない設計にします。
- SessionEnd は3回記録されました。
  同じセッションで重複したのか、確認B・Cのセッション分なのかは未確認です。
  段階2では SessionEnd を使いません。

公式ドキュメントとの差分です。

- `tool_response` は公式ドキュメントに記載がありませんが、実際には渡されます。
- V3 のツール名は公式ドキュメントに記載がありませんでした。実際は上の表のとおりです。

## 3. 書き込み先の制限

**判定：エージェント定義の `permissions` で確認済み。headless での挙動は未確認。**

確認方法は、`probe/template/.kiro/agents/kiroku-probe.json` で起動した対話セッションです。
`fs_write` を `allowed/**` だけ許可し、それ以外を拒否する規則を書きました。

- `allowed/a.txt` の作成：成功
- `denied/b.txt` の作成：失敗

公式ドキュメント（kiro.dev/docs/permissions/）で確認した点です。

- ワークスペース用の `permissions.yaml` は `~/.kiro/workspace-roots/<hash>/` にあり、リポジトリの外です。
  リポジトリに入れて配布できるのは、エージェント定義の `permissions` だけです。
- headless では `--trust-tools=<ツールID>` でツール単位の許可ができます。
  認証には `KIRO_API_KEY` が必要で、ハーネス無指定では V2 で動くため `--v3` の明示が要ります。

シェルの規則についても、実機で次を確認しました（kiro-cli 3.2.1）。

- `deny` の規則に `exclude` を書くと、除外したコマンドは拒否されません。
- `;` でつないだコマンドは分割して1つずつ判定され、1つでも拒否に当たると全体が実行されません。
  拒否されると、`tool_response` は `Exit Code: -1` になり、出力に
  `Blocked by a permission rule (...); the user did not reject it. Source: agent-profile. Triggered by: "<該当のコマンド>".` と入ります。

段階2では headless を使わず、専用エージェントの対話セッションで振り返ります。
headless での制限は、段階3で自動化するときに確認します。

## 4. skill をスラッシュコマンドで呼べるか

**判定：確認済み。**

確認方法は、`probe/template/.kiro/skills/kiroku-probe/SKILL.md` を置いた状態での `/kiroku-probe abc` の入力です。

- 既定のエージェント：`KIROKU-PROBE-SKILL-LOADED abc` が返りました。後ろの文字も渡ります。
- 専用エージェント（`resources` に `skill://.kiro/skills/**/SKILL.md` を指定）：同じく成功しました。

公式ドキュメント（kiro.dev/docs/skills/）の記述とも一致します。
カスタムエージェントは skill を既定で読まないため、`resources` への指定が必要です。

## 5. テストフレームワークと実行経路

**判定：未確認。**

テストの実行経路は導入先のリポジトリごとに異なり、このプロジェクトからは確認できません。
また、どのリポジトリでも使えることを目標にしたため、特定のフレームワークを前提にしません。

代替案として、導入先ごとの設定ファイルで次を宣言する方式にします。

- テスト実行とみなすコマンドのパターン
- 機械可読なレポートの場所と形式（まず JUnit XML に対応）

設定が無いリポジトリでは、テスト結果の取り込みを飛ばし、ツール失敗と書き込みファイルの記録だけを行います。
導入先での確認は、段階2の完了条件（実セッションでテストを成功・失敗させる）で行います。

## 参照した公式ドキュメント

- kiro.dev/docs/hooks/ 、/docs/hooks/types/ 、/docs/hooks/actions/ 、/docs/cli/v3/hooks-migration/
- kiro.dev/docs/cli/headless/
- kiro.dev/docs/permissions/
- kiro.dev/docs/custom-agents/configuration-reference/
- kiro.dev/docs/skills/ 、/docs/reference/slash-commands/
- kiro.dev/docs/cli/chat/session-management/

## 2026-11-14：デモで見つかった不具合の修正

実装・検証は一時作業コピーで行い、導入先の過去のイベント、ノート、レポート、台帳は書き換えていません。

### 設計の根拠

- [KiroのPreToolUse／PostToolUse](https://kiro.dev/docs/hooks/types/) と実測済みのフック入力に基づき、テスト実行前のレポート状態と終了後の状態を比較する。フックで利用できる一意のtoolCallIdは実測していないため仮定せず、セッション・ツール入力の識別値で対応づけ、同じ結果ファイルへの重複実行は結果不明にする。
- [エージェント設定](https://kiro.dev/docs/custom-agents/configuration-reference/) では権限はエージェント単位。skillをロードしただけでは専用エージェントの確認にならない。実測したセッションメタデータの `agentMode` と `KIRO_SESSION_ID` の照合を起動手順に加えた。
- 類似実装として [bashlex](https://github.com/idank/bashlex) のREADMEとGitHubの情報を確認した。入力を実行せず構造を解析する考え方を採用した。標準ライブラリだけを配る方針からパッケージやコードは取り込まず、[shlexの公式資料](https://docs.python.org/3/library/shlex.html) が完全なシェル解析ではないことも踏まえ、単純な構文に範囲を限定した。

### 自動検証で確認したこと

`python3 -B -m unittest discover -s tests`：108件成功。

- 最新レポートに案が0件の場合の未判断一覧・短い案番号の扱い。過去レポート名を明示すれば参照できること。
- `cd DIR &&` とリダイレクトの有無での集約・追跡。数値引数と引用した演算子を保持すること。旧キーが曖昧な場合は0回と誤表示せず判定を保留すること。
- heredoc、検索、echo、Pythonの `-c` 内のテスト名をテスト実行として記録しないこと。`aggregate.py`への言及だけで、その後の開発記録を除外しないこと。
- 古いJUnit、実行前フックの欠落、結果ファイルへの重複実行を結果不明とすること。結果不明の理由を集約に表示すること。
- A→B→Aを3回の書き込みと数え、二重フックだけをまとめること。最古の残存イベントから記録開始の理由を断定しないこと。
- 導入診断が、検出設定と直接のunittest実行の不一致を検出すること。
- レポートの機械検証値と対象の型を照合し、改変された数値や存在しない型を検出すること。
- 一時導入先で実際のunittestランナーを4回実行し、成功→回帰候補→成功→Red候補をフック入力から確認した。レポートの機械検証と、その次の「案なし」レポートで古い案を誤採用しないことまで通して確認した。これはKiroのモデル実行を伴わない連携テスト。

skillの形式検査も成功した。自由文の意味や提案の妥当性は機械検証で保証しないため、設定・ランナー・権限の本文との照合をskillに残している。

### 未検証

この作業環境の `kiro-cli whoami` は `Not logged in`。`kiro-cli agent validate --path kit/.kiro/agents/kiroku-retro.json` もログイン要求で終了した。そのため、更新後の専用エージェントをKiro上で起動する試験、実際の書き込み・シェル制限、追加したPreToolUseの実発火、LLMが作る新レポートの品質は未検証。

ログイン済みの環境で更新したkirokuを導入し、[デモ手順](demo-scenario.md) に従って4本のフック、JUnit取り込み、専用エージェント起動、保存後のレポート検査と本文の照合を実施する。Windows／IDEも引き続き実機未検証。

## 2026-11-14：CLI 3.3.0での実機確認

認証を再確認したところGitHubでログイン済みだったため、上の「未検証」で残した項目を、一時プロジェクトで継続して確認した。元のデモの記録・ノート・レポート・設定9ファイルはハッシュで保持を確認した。

### 環境と確認方法

- Kiro CLI 3.3.0、Linux（WSL2）、V3の対話セッション。
- 記録用の一時プロジェクトでは整数定数の小さなテストを使用し、家計簿の実装はレビュー・変更していない。
- `kiro-cli agent validate --path .kiro/agents/kiroku-retro.json` は終了コード0。
- `/hooks` でPreToolUse、PostToolUse、PostFileSave、UserPromptSubmitの4本が読み込まれたことを確認。
- 記録用と振り返り用のセッションを分け、`session.json` のagentModeも照合した。
- `--trust-all-tools` は使用していない。読み取りやskillロードの承認を求められたときは対象を確認して1回だけ許可した。

### フックとJUnitの取り込み

Kiroに実際のファイル編集とテスト実行を依頼し、成功→既存テストの失敗→復元後の成功→新規テストの失敗の4回を確認した。4回すべてにJUnitのテストID別結果が記録され、結果不明は0件、record-errors.logも生成されなかった。集約で既存テストの失敗は回帰候補、新規テストの失敗はRed候補になった。意図的な失敗であることは会話に明記し、振り返りでは改善すべき型から外すことを確認した。

最初に検証用エージェントが `cd ... && python3 run_tests.py 2>&1` を発行したときは、許可されていない `cd` が拒否された。許可された `python3 run_tests.py` だけの実行は成功した。この拒否と実行成功も実際のツール結果・イベントで区別できた。

### 書き込みとシェルの制限

別の一時プロジェクトに、配布版kiroku-retroと同一のpermissionsを持つ検証用プロファイルを作った。規則の一致をJSONで比較し、プロンプトだけを拒否試験用にした。詳細なセッションログはローカルの検証記録に保存した。

| 操作 | 実測結果 |
|---|---|
| 書き込みツールでreports/access-check.txtを保存 | 成功し、内容ALLOWEDを確認 |
| 書き込みツールでプロジェクト直下forbidden.txtを保存 | deny fs_write matching **、agent-profileにより拒否。ファイルは存在しない |
| シェルでprintf KIROKU_POLICY_PROBEを実行 | deny shell matching *、agent-profileにより拒否。終了コード-1 |

実際のkiroku-retroセッションでも、許可されたaggregate・excerptの実行、notesとreportsへの書き込みが成功した。規則を緩めたり、拒否を別経路で回避したりしていない。

### レポートの実機確認で追加した修正

初回のレポートは検証値・対象の型の検査に通ったが、本文に「実行時はkiroku-live-driver以外だった可能性がある」という誤った推測があった。実際のsession.jsonはkiroku-live-driverだった。数値検査の成功だけでは本文の正しさを保証できないことを、実機でも確認した。

このため、セッション一覧・要約・レポート検証値にsession.jsonのagentModeを出す修正と回帰テストを追加した。取得不能なら未確認とし、agent-profileという拒否文から名前を推測しないことをskillに明記した。セッション内の途中切り替えまでは復元していないため、その限界も表示する。継続方針1回の明示と、繰り返し指摘された経緯も区別するよう手順を補強した。

追加後の配布元のテストは109件成功、デモ側の回帰検証は6件成功。初回レポートは証拠として保存し、修正後の新しい専用セッションで振り返りを再実行した。

2回目は記録エージェント名が正しくなり、構造化した検査にも成功したが、前回レポートの「会話でその都度伝えている」という未確認の説明までコピーされていた。引き継ぎの根拠を元のノート・ログへ戻して照合する手順を追加し、型の見出しを「観測した型・継続方針」、本文を「確認できたこと」と「原因の見立て」に分けた。方針の明示だけなら原因は「該当なし」とする。

最後の専用セッションでは、元のノートを実際に読み直してから2案を引き継いだ。最終レポートの `--check-report --require-agent` が成功し、4回のテスト、14イベント、1セッション、未読0件、記録エージェント名が実記録と一致した。本文は継続方針の1回の明示として書かれ、上記の未確認の繰り返し説明はなくなった。採否は記録しておらず、ledger.jsonlも作成されていない。

記録と操作制限は通常の対話承認のままで確認した。最後のレポート再実行のみread_file・execute_bash・fs_write・load_resourceを起動フラグで事前承認したが、プロファイルのdeny規則は変更せず、実際には読み取り承認も都度求められた。これを操作制限の検証結果と混同しない。

今回の自由文の改善は、この実例での確認である。提案の効果は採用後の新規発生で確認する必要があり、過去分を含む集計欄の件数がすぐ0になることを判定基準にはしない。提案は未採用なので、効果自体は未測定。

### 手引きの現行仕様への訂正

[現行の公式リファレンス](https://kiro.dev/docs/custom-agents/configuration-reference/#disabling-default-resource-inheritance) では、カスタムエージェントは既定でsteering・skills・AGENTS.mdを継承する。上の段階1の「skillを既定で読まないためresourcesが必要」という記述は現行仕様には適用しない。配布するharness-guide.mdとデモ期待結果を修正し、resourcesの未記載だけを欠陥とせず、継承の無効化や読み込み条件の実際の不一致を確認する方針にした。

### レビュー後の修正（自動テストのみ）

- 実行後のフックが来なかった実行開始の記録が state に残り、以後の実行がすべて `ambiguous_test_run` になる不具合を直した。記録に時刻を持たせ、`test_start_max_seconds`（最初の値 3600 秒）を過ぎたものを次の実行前に捨てる。時刻の無い記録も捨てる。
- `commands.py` が無い導入先で `record.py` がトレースバックを出して終了コード 1 で終わる不具合を直した。終了コード 0 で終わり、`record-errors.log` に残す。

- 起動先で照合できないテスト実行（パイプ、`uv run` などの前置き、展開）が、どこにも出ずに消えていた。コマンドの文面でも `match` を照合し、実行の前後でレポートが変わったときだけ結果を取り込む。変わらなければ数えず、記録不足として出す。
- 解釈できないコマンドの失敗が全文のハッシュで別々の型になり、繰り返しを数えられなかった。最初の区切りまでを見出しにし、それも解釈できなければ先頭3語でまとめる。

- 旧版のレポートの対象（先頭3語）に完全に一致する実行があると、同じ先頭3語のほかの実行が数えから外れ、追跡対象が黙って狭まっていた。検証値の無いレポートを旧版とみなし、複数の見出しに分かれるときは「数えられない」とする。

いずれもフック入力を模したテストで確かめた。Kiro を異常終了させたときに実行後のフックが来ないこと自体は、実機で確かめていない。期限の 3600 秒は実測に基づく値ではない。

### 引き続き未検証の範囲

Windows／IDE、headlessでの制限、長期運用や提案採用後の効果はこの実機試験の対象外。自由文の意味や提案の妥当性は引き続き本文の根拠照合が必要で、機械検査がすべてを保証するものではない。

## 2026-11-14：PR #2 の再レビューと demo での再検証

対象は PR #2 の `125dd12fdf8c16dfa682f7842b29c220bd6a1ec8`。main は `git pull --ff-only` で最新を確認し、PR は別の一時 worktree で検査した。既存レビューの原文は [PR コメント](https://github.com/yuyumama/kiroku/pull/2#issuecomment-6870984868) を参照。

### 自動検証

- PR の `python3 -B -m unittest discover -s tests`：115件成功。
- 既存レビューの4件（文面による検出へのフォールバック、残留した実行前記録の期限、複合コマンドの見出し、補助モジュール欠落時の終了コード0）は、コードと自動テストで確認した。
- 追加で、旧キーの追跡が過少集計になる問題を再現した。旧版の対象 `tool:npm run build` に通常実行と `--production` 付き実行の失敗を各1件与えると、旧 main の `recent` は2、PR版は1。完全一致するキーを先に返すため、旧キーの曖昧性チェックに届かない。[追加の指摘](https://github.com/yuyumama/kiroku/pull/2#issuecomment-6871299745) として報告し、実装は変更していない。

### Kiro CLI で確認したこと

Linux、Kiro CLI 3.3.0、V3 の対話モード。`examples/demo/` を一時ディレクトリへコピーし、PR版を導入した。家計簿の実装と既存11テストを使用した。元の demo と配布コードは変更していない。

- `/hooks`：kiroku の4本と demo の2本、合計6本を確認。専用エージェントの `agent validate` は終了コード0。
- 通常実行、同じコマンドの再実行、`python3 run_tests.py 2>&1 | tail -20` は、それぞれ11件成功の JUnit を取り込んだ。
- `echo run_tests.py && grep run_tests.py README.md` はテスト実行に加算されず、`report_stale`（文面で一致）の記録不足として表示された。
- Kiro の書き込みツールで家計簿の集計を意図的に壊し、既存テスト3件の失敗を「回帰候補」と確認。復元後は11件成功。
- 新規の失敗テスト1件を追加し、「Red 候補」と確認。修正後は12件成功。
- 合計7回のテスト実行、書き込み4回、19イベント、開発セッション2件。JUnit を取り込めなかった実際のテスト実行は0件。記録不足の1件は上記の名前への言及。実行前記録の残留、`record-errors.log`、kiroku 配下の `__pycache__` はなし。
- 別の demo コピーへ旧 main を導入し、Kiro を起動したまま `--update` を実行した。今回の CLI では再起動なしで追加の実行前フックが働き、次の11件成功を取り込んだ。`config.json` の内容も保持された。既存環境への影響は、この確認範囲では「`--update` だけで済む」。

記録用セッションは通常の対話承認で実行した。一部の起動で `--trust-tools` も指定したが、実際には書き込みとシェルの承認を都度求められた。`--trust-all-tools` は使用していない。

### 振り返りレポート

別の `kiro-cli chat --v3 --agent kiroku-retro` セッションから `/kiroku-retro` を実行した。`session.json` の `agentMode` は `kiroku-retro`。`--require-agent` と、保存後の `--check-report --require-agent` は成功し、外からの再検査も終了コード0だった。

- レポートは一時 demo の `data/reports/2026-11-14-1255.md`。19イベント、開発セッション2件、未読0件、ノート2件が実記録と一致。提案は1件。
- 振り返り中に、事前にハッシュを控えたコード・設定など40ファイルの内容とファイル集合は完全一致した。ハーネスの変更、新たな対象外ファイル、台帳の作成はなかった。提案は採用していない。
- 最初の集約時に `fs_read` の `.` を、その後の本文確認時に Read ツールを、このセッションだけ許可した。専用プロファイルの書き込み・シェルの deny 規則は変更していない。これを対象外操作の拒否試験と同一視しない。

機械検査は成功したが、本文には次の問題があった。生成物は訂正せず、実機試験の証拠として一時 demo に保持している。

- 個別のユーザー共通ハーネスの所在から、実際に存在する `~/.kiro/` 全体を「存在しない」と記載した。
- `make test` を `python3 run_tests.py` に置き換える提案について、「文字数の増減なし」と記載した。実際は11文字増える。
- 冒頭の「読めなかった範囲：なし」と、表の `add-category/SKILL.md`「本文未確認」が食い違っていた。
- 意図的な回帰3件と Red 1件は問題から外したが、同じく意図的と明記した `make test` の試験は型と改善案にした。既存 steering と実行方法の不一致自体は実在するものの、意図的な失敗を型にしないという skill の指示への追従は不十分だった。

検証値の一致は自由文の正しさを保証しない、という既存レビューの限界を今回の実測でも確認した。

### 未検証

Windows／IDE、headless、Kiro の異常終了そのもの、3600秒という期限の実運用上の妥当性、長期運用、提案採用後の効果は未検証。demo シナリオの全会話を再現した試験ではなく、PR の変更点に絞った動作確認。対象外操作への権限拒否試験は今回再実施していない。

### 追加修正 `93d0fc6` の確認

前回指摘した旧キーの過少集計を修正したコミットを、別 worktree で再レビューした。`kiroku-facts` のあるレポートは現行キー、無いものは旧キーとして解釈する。今回の差分に skill・フック・権限の変更はない。

- `python3 -B -m unittest discover -s tests`：117件成功。
- 旧版の `tool:npm run build` に通常実行と `--production` 付き実行の失敗を各1件与える前回の再現例は、未判断の `recent` が `None` になった。採用・見送り後も「対象の型を数えられない」となり、黙って1件だけを数える問題は解消した。
- 現行レポートでは完全一致だけを数える。通常実行を採用前、`--production` 付き実行を採用後に置いた模擬記録では、採用・見送りとも前1件・後0件となった。
- 前回の実機 demo のコピーに `--update` を実行し、更新は集計・台帳の2スクリプトのみ。既存の設定・データ・ノート・レポートはハッシュで完全保持を確認した。
- 元の実機 demo のパスを `--root` に指定し、新版の集計スクリプトで前回のレポートを再検査して終了コード0。別パスのコピーでは検証値内のパスが変わるため、その不一致を製品の回帰とは扱っていない。

今回の確認は集計・台帳と更新処理が対象で、Kiro の新しい会話・レポート生成は再実行していない。旧キーの P2 は修正を確認した。前回のレポート本文の誤りは今回の変更対象に含まれず、改善は未確認のまま。

## 2026-11-14：拡張デモの確認

PR #2 マージ後の `be808dd` を対象に、[拡張ケース D01〜D16](demo-cases.md) を追加した。配布コード・元の家計簿は変更せず、標準ライブラリによる8件の検査と、期待結果を配らない別の一時 demo を用意した。既存導入先への影響は「なし」。

### 自動検査と実機の区別

- 全体の `python3 -B -m unittest discover -s tests`：125件成功。
- 追加の `test_demo_cases.py`：8件成功。家計簿11テストを本物のランナーで動かし、再実行・名前への言及・回帰3件・Red1件・復元・日本語パスのA→B→Aを検査した。フック入力は模擬で、Kiro の実発火を証明するものではない。
- 模擬会話と事前に付けたノートを使い、別セッション2件、同一セッション内2回、継続方針1回、確認事項、根拠欠落、架空の引用、ノートの再利用、最新の案なしを検査した。意味ラベルを LLM が付けた証拠とはしていない。

### Kiro CLI 3.3.0 の実機で観測したこと

Linux / V3 対話モード。作業担当2セッション、reviewer、停止フックの probe を実行した。作業担当と probe は一時 demo にだけ置いたプロファイルで、読み取り、家計簿・テストへの書き込み、必要なコマンドだけを事前承認した。サブエージェント、`--trust-all-tools` は使用していない。reviewer は配布サンプルの定義を使い、読み取りをセッション内で承認した。

- add-category skill により医療カテゴリとテストが追加された。Kiro は先にテストを失敗させ、実装後に12件成功を確認した。最初の全成功より前の失敗のため、集約の分類はRedではなく「その他」。基準が無いという理由も一致した。
- JUnit を出す実行は4回、イベント20件、開発セッション4件、書き込み2回。対応するJUnitの不足は0件。
- 2セッションで報告の順序を指示した。1セッションではコミットの日本語を継続方針として明示し、比較月は今回限りと明示した。もう一方では整数円の既存規則を維持するよう伝えた。これは試験者が与えた筋書きで、自発的な繰り返し失敗の証明ではない。
- reviewer は最初に存在しない HEAD~1 を参照し、make test と python でも失敗した。その後 unittest を直接実行した。補足後は README の run_tests.py で12件成功を確認し、python-style.md の条件が実配置に合わないことを認識した。最初から規約を読んでいたとは扱わない。

### 停止フックと記録の不一致（D16）

リモートの無い使い捨てGitで `git push --force --dry-run` を1回だけ要求した。実ログではリダイレクト付きの呼び出し1件、tool_call の status は denied、tool_result は success=false / content="{}"。guard-shell の ContextualHookInvoked があり、停止自体は確認できた。PostToolUse の kiroku フックはログに無く、そのセッションの events.jsonl は prompt だけだった。record-errors.log も生成されなかった。

集約には拒否も recording_gap も出ず、停止の事実が記録不足として表に出なかった。D16 は「停止は確認、記録は不一致」と判定した。エージェントの説明には一時的に実ログと食い違う部分もあり、判定にはツール状態・フック・イベントを使用した。回避や実際の push は行っていない。

理由を `{}` のツール応答から推測しないため、さらに別の一時コピーで監査用ラッパーを挟んで再実行した。ラッパーは元の guard_shell.py に同じ標準入力を渡し、出力・終了コードをそのままKiroへ返し、コマンドと終了コードだけを監査に残す（標準エラーも停止理由として記録）。元スクリプトは終了コード2、標準エラー「止めました: 強制的な push はしません。」だった。この回も events.jsonl は prompt だけで、停止の未記録を再現した。監査用のフック定義とスクリプトはこの別コピーだけに置き、配布コードや振り返り対象の記録は変えていない。

### 1回目の振り返り

別の専用セッションから `/kiroku-retro` を実行した。期待表や模擬会話は一時 demo にコピーしていない。読み取りの事前承認はそのセッションだけで、配布版のプロンプト・書き込み／シェルの deny 規則は変更していない。

- `2026-11-14-2111.md`、ノート4件を生成。`--require-agent` と保存後の `--check-report --require-agent`、外からの再検査に成功した。
- 「完了報告の書き方」は別の2セッション、「コミットメッセージの言語」は継続方針1回として候補になった。引用の不一致は0件。一時的な比較月と、整数円の既存規則は候補・改善案にしていない。
- 改善案は、報告形式、日本語コミット、誤ったテスト手順の3件。resources 未記載だけを欠陥にする案は出なかった。python-style の配置条件の不一致は確認されたが、規約違反を起こした因果までは今回の変更から証明していない。
- 振り返り前後でコード・設定等41ファイルの内容と集合が完全一致。台帳は作成されていない。

本文品質には不一致が残った。`make test` の置換を「文字数変化なし」としたが実際は11文字増える。「未確認範囲なし」と表の未確認2件も矛盾した。報告形式の2回の明示を未遵守や原因の断定に広げた説明は、引用が実在することとは別に評価が必要だった。親ディレクトリの不在への誤った一般化は、この回は起きていない。

### 2回目の振り返りと解消済み案（D12・D13）

試験者が一時 demo の testing.md のコマンド1箇所だけを修正し、別の専用セッションで再実行した。採用の実運用ではなく、既に直った設定との比較試験であり、採否は記録していない。

- `2026-11-14-2118.md` を生成。専用セッション内・外のレポート検査はいずれも成功。
- ノート4件は1回目とハッシュが完全一致し、今回のノート作成0件、未読0件。20イベント・4セッション・引用の回数も増えなかった。
- 報告形式と日本語コミットの2案を、前の案番号を示して引き継いだ。テスト手順の案は現在の設定が修正済みだと確認して外し、「引き継がなかった案」に理由を記載した。同じ古い失敗が期間内に残ることを再発とはしていない。
- 保全対象の差分は試験者が直した testing.md だけで、振り返りエージェントによるハーネス変更・台帳の作成はない。
- 未確認の add-category skill を表に残しながら冒頭では「未確認範囲なし」とする不一致は2回目にも残った。1回目の未遵守・原因の説明も引き継がれ、意味の確認は引き続き必要。

### ケースごとの判定

| ケース | 判定 | 今回の証拠と限界 |
|---|---|---|
| D01 | 合格（実機） | カテゴリ追加、追加テスト、JUnit4回を照合 |
| D02 | 合格（抽出・集計） | 同じ報告形式の引用2件を別2セッションとして照合。原因の説明の妥当性とは別 |
| D03 | 合格（模擬） | 同一セッション2回はセッション1件、候補にしない |
| D04 | 合格（実機） | 継続方針1回を候補にし、繰り返しの違反とは記載していない |
| D05 | 合格（実機） | 今回限りの月を恒久ルールにしない |
| D06 | 合格（実機） | 整数円の既存規則を重複追加しない |
| D07 | 合格（仕様照合・実機） | resources 未記載だけを欠陥とする案は無い |
| D08 | 前提不足（因果の判定） | 条件の不一致は確認。今回の差分だけでは規約違反の原因まで証明できない |
| D09 | 合格（連携試験） | 再実行・パイプ・名前への言及を区別。Kiroの全シェル構文は未検証 |
| D10 | 合格（連携試験） | 回帰3件・Red1件・復元を確認。実機の最初の失敗は基準不足の「その他」 |
| D11 | 合格（模擬） | 架空の引用・欠落ログを根拠に候補を増やさない |
| D12 | 合格（実機） | ノート4件を保持、未読0、2案を引き継ぎ |
| D13 | 合格（実機） | 手で直した設定を読み、解消済みの案を外した |
| D14 | 合格（模擬） | 最新の案なしで古いP1を復活させない |
| D15 | 不一致（実機） | 文字数や未確認範囲、根拠からの説明の広げ方に問題が残る |
| D16 | 不一致（実機） | フック停止は確認したが、イベント・不足表示が残らない |

この表の合格には模擬入力による検査を含む。全ケースでLLMや実機が期待どおりだったという意味ではない。Windows／IDE、headless、実運用で指摘が減る効果、長期の追跡は今回も未検証。生成物は訂正せず一時 demo に保持し、生ログ・個別セッションIDをリポジトリへ複製していない。

### 初期キットの廃止

`probe/` の機能はデモの使い捨てコピーで確認できる。違いは診断手順の有無であり、デモでは代替できないという判断は誤りだった。今回実施した停止フックの監査も、その代替方法の実例に当たる。
独立した初期キット8ファイルを削除し、再確認の手順を demo-cases.md に統合した。過去の検証記録は保持し、現行手順への案内を付けた。古い診断出力のgit除外は継続する。配布ファイルや既存設定・データへの影響はない。
この整理によって9種類のフックの再実測や、D15・D16の問題の修正を行ったとは扱わない。

## 2026-11-15：D16 の改善案を判断するための実機診断

[PR #3 の依頼](https://github.com/yuyumama/kiroku/pull/3#issuecomment-6880451786)に対応した。今回は確認のみで、配布コードと元のデモは変更していない。既存導入先への影響は「なし」。

### 1. 環境と方法

- Ubuntu 22.04.4 LTS、Linux 6.8.0-52-generic、Kiro CLI 3.3.0、V3 対話モード。
- 最新取得した main の `be808dd` を一時領域へ展開し、その `examples/demo/` のコピーへ同じ main のインストーラーで導入した。比較用のコピーはそれぞれリモート未設定の使い捨て Git とした。
- 一時エージェントに読み取りと指定した無害なコマンドの allow、固定 printf の deny／ask を設定した。承認拒否は、試験操作として承認画面の「Deny once」を選択した。人間がその場で操作したという意味ではない。
- Stop 観測フックと、終了コード1の試験フックは一時コピーだけに置いた。配布スクリプトと guard_shell.py が main と同じ内容であること、リモートが無いことを確認した。実際の push・削除、拒否の回避、`--trust-all-tools` の使用は無い。
- Kiro がツール呼び出し前に拒否したターンや、呼び出さず成功を申告したターンがあった。これらは実行・停止の証拠から除外した。以下は実際の tool_call／tool_result、フック、state、イベント、JUnit を照合した結果であり、エージェントの完了報告だけでは判定していない。
- 生ログ、発話本文、個別 ID、個人設定は転載していない。以下のコマンドと出力例は、この試験の固定値である。

### 2. 確認1：キー・型・対応関係

messages.jsonl の各行の外枠は `id: string / timestamp: string / payload: object`。以下は payload のキーで、型名は JSON の型に揃えた。

| payload | キーと型 |
|---|---|
| tool_call（フック停止） | `type, toolCallId, toolName, status, kind, executionId, turnId, actionType, title: string`、`args, _meta: object` |
| その args | `command, cwd: string`、`description, timeout: null`、`run_in_background: boolean` |
| その _meta | `kiro: object`。その中に `agentMode, toolOrigin: string` |
| tool_result | `type, toolCallId, content, executionId, turnId: string`、`success: boolean`、`_meta: object` |
| その _meta | `kiro: object`。その中に `agentMode: string` |
| ContextualHookInvoked | `type, hookId, operationId, name, hookActionType, status, executionId, turnId: string` |

固定コマンド `git push --force --dry-run` の tool_call は `toolName="execute_bash" / status="denied"`、tool_result は `success=false / content="{}"` だった。content は空オブジェクトではなく、2文字の**文字列**である。guard_shell.py に同じコマンドを標準入力で与える直接検査では終了コード2、標準出力なしを確認した。

フック行には `name="guard-shell"` があり、`hookActionType="runCommand" / status="completed"` だった。**終了コード・標準エラー・停止を示す専用キーは無い。** 後述の許可されるコマンドで guard-shell が通過した場合も、このフック行の status は completed である。名前が存在するだけで「このフックが止めた」とは判定できない。

対応と行の順序は次のとおり。

- tool_call と tool_result の `toolCallId / executionId / turnId` は一致した。
- フック行にも同じ `executionId / turnId` があったが、`toolCallId` は無い。`operationId` は tool_call の `toolCallId / modelToolUseId / executionId / turnId` のいずれとも一致しなかった。`hookId` はフックの定義元・位置を示すもので、呼び出しの対応キーではない。
- 単独フック停止の行順は、prompt フック → kiroku-record-test-start → guard-shell → tool_call → tool_result → Stop 観測フック → turn_end。PostToolUse の kiroku-record-tool は無い。
- 今回は各判定対象ターンに shell 呼び出し1件だった。同一ターンの複数・並行呼び出しを、フックへ一対一に結びつけられるかは未確認。ターンの一致を個別ツールの一致と扱うことはできない。

### 3. 確認2：4種類の対照

全4ケースの tool_result は `success=false`。各ターンの prompt は全ケースで記録されたため、イベント欄ではその後の追加記録を示す。

| ケースと固定コマンド | tool_call / 判別に使える情報 | tool_result.content の固定値・特徴 | PostToolUse | events.jsonl の追加記録 |
|---|---|---|---|---|
| フック停止：`git push --force --dry-run` | `status=denied`。policyDenial 無し。guard-shell の発火あり | `"{}"`。終了コード・理由なし | 無し | 無し（prompt のみ） |
| エージェント規則の拒否：`printf KIROKU_DENIED_PROBE` | `status=denied`。`_meta.kiro.policyDenial.effect=deny / source=agent-profile` | `Blocked by a permission rule (deny shell matching "printf KIROKU_DENIED_PROBE"); the user did not reject it. Source: agent-profile.` と `Exit Code: -1` | 有り | `tool_denied` 1件 |
| 承認画面で Deny once：`printf KIROKU_CANCEL_PROBE` | `status=denied`。policyDenial 無し。対応する `interaction_resolved` は `outcome=selected / selectedOption=reject` | `The user rejected this tool call.` と `Exit Code: -1` | 有り | `recording_gap` 1件、`reason=unknown_not_run` |
| 通常の実行失敗：`ls /no/such/kiroku-d16` | `status=failed`。policyDenial 無し | `ls: cannot access '/no/such/kiroku-d16': No such file or directory` と `Exit Code: 2` | 有り | `tool_failure` 1件、`exit_code=2` |

フック停止以外の3ケースでも、事前の kiroku-record-test-start と guard-shell は completed として記録された。行順は、両 PreToolUse → tool_call → tool_result → kiroku-record-tool → Stop。承認拒否では両 PreToolUse の後、tool_call より前に `pending_interaction → interaction_resolved` が入った。

3ケースの tool_call の共通キー・型は確認1と同じで、加えて `modelToolUseId: string` があった。args は `command: string / run_in_background: boolean`。これら3ケースでは `cwd / description / timeout` は args に無かった。フック停止時の args の補完や modelToolUseId の有無は観測上の差だが、停止理由を表す契約とは確認できていない。

権限拒否にだけあった `policyDenial: object` の中は、`capability, resource, triggeringResource, effect, scope, source: string`、`matchedRule: object`。matchedRule の中は `capability, effect: string / match: array<string>` だった。各 tool_result と ContextualHookInvoked のキー・型は確認1と同じだった。

承認拒否の `interaction_resolved` は `type, toolCallId, outcome, selectedOption, executionId: string / consentRound: number（整数）`。その toolCallId は拒否された tool_call と一致し、利用者による選択の証拠を持つ。これは「不明な拒否」の推測とは区別できる。

この4件は区別できた。ただし、`denied + "{}" + フック発火 + PostToolUse不在` は今回の観測上の組み合わせであり、あらゆる拒否・異常終了からフック停止だけを一意に確定できるという証明ではない。既存の tool_denied、recording_gap、tool_failure をもう一度拒否として加算すると二重計上になる。

### 4. 確認3：複合コマンドと残留した開始記録

削除を含む例の代わりに、`git push --force --dry-run ; python3 run_tests.py` を使った。各コピーで先に guard_shell.py へ標準入力で渡し、終了コード2になることを確認した。下表の順序は、ログに記録された両 PreToolUse 行の順序である。

| 一時コピーの定義ファイル名 | 両フックの記録順 | 停止直後の test_starts | 次の通常テスト |
|---|---|---|---|
| `kiroku.json / project.json`（既定） | kiroku-record-test-start → guard-shell | 1件残留 | 実行・JUnitとも11件成功、記録は ambiguous_test_run |
| `a-project.json / z-kiroku.json` | guard-shell → kiroku-record-test-start | 1件残留 | 実行・JUnitとも11件成功、記録は ambiguous_test_run |
| `a-kiroku.json / z-project.json` | kiroku-record-test-start → guard-shell | 1件残留 | 実行・JUnitとも11件成功、記録は ambiguous_test_run |

複合コマンドは各コピーで実際の tool_call を1件ずつ確認し、`denied / success=false / content="{}"`。PostToolUse は無く、そのターンのイベントは prompt のみだった。停止前に控えられたレポート一覧は、下記のとおり空だった。

停止直後の開始1件は `before=[] / report="reports/junit.xml" / count=1 / overlap=false`、`at: number` だった。`test_starts` 自体はキーが文字列、値が object の対応表である。キーの値と at の実値は転載しない。

続く `python3 run_tests.py` は各コピーで本当に実行され、終了コード0、JUnit11件・失敗0・エラー0を確認した。一方、イベントは `type=test_run / exit_code=0 / results=null / note=ambiguous_test_run`。通常実行分の開始は消費されたが、停止分は1件残って `overlap=true` に変わった。停止した開始と通常実行が重なったため、成功したJUnitの取り込みが保留になった。

3通りとも両 PreToolUse が動き、**guard-shell が先でも、後続の記録フックは動いた**。今回はファイル名の順に対応してログの順序が変わったが、一般の実行順保証や同期実行まで証明するものではない。フックの名前替えだけで残留記録を防ぐことは、今回の結果ではできない。

### 5. 追加2項目

**終了コード1**：一時コピーに、固定標準エラー `KIROKU_EXIT1_WARNING` を出して終了コード1で終わる PreToolUse を追加した。`printf KIROKU_EXIT1_PROBE` の実呼び出しは `status=completed / success=true`、出力は固定文字列、終了コード0。PostToolUse も動いた。試験フックの ContextualHookInvoked も status=completed であり、そこには終了コードや標準エラーが無い。この条件では**1は停止せず、2は停止した**。

[actions](https://kiro.dev/docs/hooks/actions/) は PreToolUse の非0終了を停止と説明する一方、[exit-codes](https://kiro.dev/docs/reference/exit-codes/) は2のみを停止と説明している。今回の CLI 3.3.0 の観測は後者と一致する。公式説明の適用範囲を他のモードまで一般化しない。[types](https://kiro.dev/docs/hooks/types/) も参照したが、今回調べた拒否時の順序・発火を補う仕様根拠にはしなかった。

**Stop**：単独フック停止と、3通りの複合コマンド停止の各ターンで Stop 観測フックが実行された。`/quit` 前にローカルの観測ファイルとログの両方で確認したため、セッション終了時だけの発火ではない。Stop 入力は `hook_event_name="Stop" / cwd: string / session_id: string` のみで、個別呼び出しの toolCallId や停止理由は無かった。

### 6. 未確認と検証の限界

Windows／IDE／headless、Kiro の別バージョン、同一ターンの複数・並行ツール、別のフック停止原因、フックのタイムアウトやプロセス異常終了は未確認。配列内のフック順序だけを入れ替える検査と、同一条件での反復・競合試験も行っていない。今回の順序はログの順序であり、内部の起動・完了時刻や直列／並列実行は未確認。

セッションログは非公開形式であり、今回のキーの意味や欠落条件を他の環境へ保証する根拠は無い。実装・更新処理の変更は行わず、A／B の集約や記録不足表示を製品へ実装しての検証もしていない。承認取り消しは Deny once を検査したもので、Escape／Ctrl+C 等は未確認。

今回のリポジトリ全体の自動テストは `python3 -B -m unittest discover -s tests` で125件成功。これは上記の実機観測と区別する。

### 7. 所見：案A・案B

**案A**：今回の4ケースを区別し、events と toolCallId 等を突き合わせて不足を検出する材料はある。ただし「guard-shell が発火した」だけでは停止理由にならず、フック行には終了コード・標準エラー・ツール単位の対応キーが無い。一般のログに対して、フックによる停止と確定して数える仕様をそのまま実装する根拠は不足している。明示的な policyDenial・承認拒否を先に区別し、残りを「理由を確定できない未実行／記録不足」として扱う案なら検討できる。個別フックへの原因の断定は避ける必要がある。

**案B**：今回の3通りでは、別のフックが停止しても開始を残せたので、PostToolUse が来なかったことを「記録不足」に出す補助方式は実装を検討できる。ただし現行の開始記録はテスト対象だけであり、確認1の単独コマンドを覆っていない。全対象への開始記録、対応づけ、残留記録を閉じるタイミングが別途必要となる。Stop の発火はその判断材料になるが、Stop 入力単独では呼び出しや拒否理由を特定できない。

現状の開始記録を残すだけでは、次の正常なテストまで保留になることを実測した。A／B いずれを選んでも、拒否済みの記録との二重計上と、停止した開始記録が後続テストへ与える影響を扱う必要がある。今回はこれらの事実を報告するまでとし、修正はしていない。
