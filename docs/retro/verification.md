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

## 2026-11-15：Issue #4 の追加実機診断

[指定コメント](https://github.com/yuyumama/kiroku/issues/4#issuecomment-6887535610)の4項目を確認した。今回は診断と文書記録のみで、`kit/` と `examples/demo/` は変更していない。既存導入先への影響は「なし」。

### 1. 環境と観測方法

Ubuntu 22.04.4 LTS、Linux 6.8.0-52-generic、Kiro CLI 3.3.0、V3 対話モード。取得した main `6519776` を一時領域へ展開し、同じ main のデモとインストーラーで、取り消し・背景実行・複数呼び出し・回数確認用の4コピーを作った。すべてリモート未設定の使い捨て Git である。

一時プロファイルで読み取り、固定コマンド、診断用テキストファイルの書き込みを許可し、取り消し対象の printf と sleep は ask にした。`--trust-all-tools`、実際の push・削除、拒否された呼び出しの回避は行っていない。

各コピーに Stop と UserPromptSubmit の観測フックを追加した。`.kiro/kiroku/data/diagnostics/boundaries.jsonl` に書くのは `at: string` と `hook_event_name: string` だけで、発話やセッション ID は含めない。実行前後の kiroku フックには一時ラッパーを挟み、元の record.py に同じ標準入力を渡し、標準出力・標準エラー・終了コードをそのまま返した。別の診断ファイルに呼び出し時刻、入力の型、Exit Code の有無、JUnit の状態、test_starts の前後を控えた。配布スクリプトと元の guard_shell.py の内容が main と一致することも確認した。

以下は実ログ・観測ファイル・state・イベント・JUnit を照合した結果である。ツール未呼び出しの拒否や完了申告は実行証拠にしていない。生ログ、発話本文、個別 ID、個人設定、絶対パスは転載していない。

### 2. 確認1：取り消し方ごとの対照表

最初の4行は前回の比較を引き継ぐ。権限規則の拒否、Deny once、通常失敗は今回は再実施していない。フック停止は今回の複数呼び出しの検査でも再確認した。全行の tool_result.success は false。イベント欄は各ターンの prompt を除いた追加記録である。

| ケース | tool_call.status / policyDenial | 同じ toolCallId の interaction_resolved | tool_result.content の特徴 | kiroku-record-tool | events.jsonl の追加記録 |
|---|---|---|---|---|---|
| フック停止（前回・今回） | denied / 無し | 無し | 文字列 `"{}"`、Exit Code 無し | 無し | 無し |
| エージェント規則の拒否（前回） | denied / 有り、source=agent-profile | 無し | `Blocked by a permission rule …`、Exit Code: -1 | 有り | tool_denied |
| 承認画面の Deny once（前回） | denied / 無し | outcome=selected、selectedOption=reject | `The user rejected this tool call.`、Exit Code: -1 | 有り | recording_gap、reason=unknown_not_run |
| 通常失敗（前回） | failed / 無し | 無し | 固定の存在しないパスに対する ls エラー、Exit Code: 2 | 有り | tool_failure |
| 承認画面の Escape（今回） | denied / 無し | outcome=cancelled、selectedOption キー無し | `The tool call was cancelled before it was approved.`、Exit Code: -1 | 有り | tool_cancelled、reason キー無し |
| 承認画面の Ctrl+C（今回） | denied / 無し | outcome=cancelled、selectedOption キー無し | 同上、Exit Code: -1 | 有り | tool_cancelled、reason キー無し |
| 承認後、実行中の Ctrl+C（今回） | failed / 無し | 承認時の outcome=selected、selectedOption=accept。中断用の別行は無し | `This tool was interrupted before it reported a result, so it may or may not have taken effect.`、Exit Code 無し | 無し | 無し（prompt のみ） |

今回の固定コマンドは `printf KIROKU_ESC_PROBE`、`printf KIROKU_CTRLC_PROBE`、`sleep 30`。最後のケースは Allow once を選び、実行中の表示になってから Ctrl+C を送った。承認後の待機中に割り込み、正常完了の終了コードは取得していない。中断応答が示すとおり、すべての副作用が取り消されたという意味ではない。

Escape／承認画面 Ctrl+C の interaction_resolved は、`type, toolCallId, outcome, executionId: string / consentRound: number（整数）`。selectedOption は null ではなくキー自体が無かった。承認後の Ctrl+C には承認時の selectedOption=accept が残った。

承認前の取り消し2件のログ行順は、PreToolUse → pending_interaction → interaction_resolved(cancelled) → tool_call(denied) → tool_result → turn_end(cancelled) → kiroku-record-tool だった。**turn_end の後に実行後フックの行が記録された。** 実行中 Ctrl+C は tool_result と turn_end(cancelled) があり、実行後フックの行は無かった。3件とも Stop はログ・観測ファイルの両方で0回。セッション終了まで確認した。

今回の3通りはフック停止と区別できた。ただし、承認前の取り消しは `denied / policyDenial無し / selectedOption=reject無し` にも当たる。**reject だけを除外する条件では足りず、outcome=cancelled の扱いが必要**である。

### 3. 確認2：バックグラウンド実行

一時コピーだけに `diagnostic_run_tests.py` を置いた。開始を診断ファイルへ記録し、固定の12秒待機後に未変更の run_tests.py を実行し、終了を記録するもの。名前は既存設定の `run_tests\.py` に一致する。待機は開始と完了を分離して観測するための試験値で、製品のしきい値ではない。

実際の tool_call.args は `run_in_background=true` だった。フック入力の command は `python3 diagnostic_run_tests.py`、ログの tool_call.args.command は `python3 diagnostic_run_tests.py &` となった。双方の文字列が常に同じとは限らない。

観測した順序は次のとおり。

1. kiroku-record-test-start、guard-shell。test_starts に1件作成。
2. 診断スクリプトの開始。
3. ログの tool_call、tool_result。`status=completed / success=true`、content は `Output:` の空出力と `Exit Code: 0`。
4. kiroku-record-tool。入力の tool_response にも `Exit Code: 0` があった。
5. Stop、turn_end。
6. 待機の後にJUnitが生成され、診断スクリプトが終了。

これは診断スクリプトの開始・終了時刻とフック時刻を照合した順序であり、ログ行の並びだけからプロセス開始を推測していない。**PostToolUse と Stop の両時点で、診断スクリプトの観測ファイルには start だけがあり、end はまだ無かった。** つまり背景の処理はまだ完了していなかった。

PostToolUse の直前・直後とも JUnit は未生成で、test_starts は直前1件・直後0件。イベントは `type=test_run / exit_code=0 / results=null / note=report_not_found` だった。その後、本物の JUnit に11件・失敗0・エラー0が生成されたが、セッションを終了するまで、追加の PostToolUse と test_run は無かった（それぞれ計1件のまま）。正常終了コードがあっても、それがテスト完了を意味しないことを確認した。

### 4. 確認3：同じターンの複数呼び出し

両ケースとも、対象の2件がそれぞれ別の toolCallId を持ち、同じ turnId と executionId になった。以下で Pre は kiroku-record-test-start、Guard は guard-shell、Post は kiroku-record-tool、Call／Result はツール行を表す。

| ケース | 実際の呼び出しとログ行順 | 記録 |
|---|---|---|
| 正常2件 | Pre → Guard → Pre → Guard → Call(A) → Result(A) → Call(B) → Result(B) → Post → Post → Stop | `printf KIROKU_MULTI_A` と `printf KIROKU_MULTI_B` はともに completed、success=true、Exit Code: 0。イベントは prompt のみ |
| 停止1件＋正常1件 | Pre → Guard → Call(push) → Result(push) → Pre → Guard → Call(C) → Result(C) → Post → Stop | `git push --force --dry-run` は denied、success=false、content=`"{}"`。`printf KIROKU_MULTI_C` は completed、success=true、Exit Code: 0。イベントは prompt のみ |

混在ケースで、ラッパーの入力にも push の Pre だけ、printf の Pre／Post が残った。止められた push に Post は無い。最初の依頼では Kiro が push をツールに渡さず printf だけ実行したため、そのターンは2件比較の対象外とした。表は、その後に実際の2件を確認できた別ターンの結果である。止められた呼び出し自体の再試行はしていない。

ContextualHookInvoked のキーは前回と同じで、toolCallId やコマンドを持たなかった。operationId は、対応候補のどの tool_call の toolCallId／modelToolUseId／executionId／turnId とも一致しなかった。hookId は同じ定義の発火で共通であり、呼び出し単位の識別には使えない。

正常2件では、ラッパーの時刻が `Pre(A)開始 < Pre(B)開始 < Pre(A)終了`、Post も同様に重なった。**少なくともフック処理には並行の形跡がある。** シェル本体のプロセス実行区間は測っていないため、その同時実行は未確認。ログも呼び出し単位のまとまりではない。混在ケースは順番に対応できそうな並びだったが、それを正常2件や一般の並行実行へ適用する根拠は無い。一時ラッパーでは入力の固定コマンドから対応を確認できても、既存のフック行だけにその情報は無い。

### 5. 確認4：入力数とフックの回数

1つの新規セッションで、通常入力12回と `/hooks` 2回、計14回を送信し、その後に `/quit` を1回送った。メニューは Escape で閉じた。メニューを閉じるキー操作は、新しい発話に数えていない。各入力の前後で観測ファイル・prompt 件数・ログを比較した。

このセッションでは force dry-run の依頼を2回とも Kiro がツール前に拒否した。これらをフック停止に数えず、別の一時診断フック diagnostic-count-block を追加した。固定の `printf KIROKU_COUNT_BLOCK` だけを終了コード2で止めるもの。同じセッションで `/hooks` の一覧に追加を確認し、実呼び出しの denied／content=`"{}"`、Pre の発火と Post の不在を照合した。main の guard-shell は変更していない。

| 入力の種類 | 入力回数 | UserPromptSubmit | Stop | events の prompt |
|---|---:|---:|---:|---:|
| 質問のみ・ツール無し | 4 | 4 | 4 | 4 |
| 正常なシェル実行 | 3 | 3 | 3 | 3 |
| ファイル書き込み | 2 | 2 | 2 | 2 |
| Kiro 自身がツール前に拒否 | 2 | 2 | 2 | 2 |
| 診断フックによる停止 | 1 | 1 | 1 | 1 |
| `/hooks`（一覧の開閉） | 2 | 0 | 0 | 0 |
| 終了前までの合計 | 14 | 12 | 12 | 12 |
| `/quit` の増分 | 1 | 0 | 0 | 0 |

ログ上も user と turn_start は各12件で、スラッシュコマンドは user 行を作らなかった。質問、正常ツール、書き込み、フック停止の**通常終了したターンでは抜けは無かった**。全入力14回を分母にすると12回になる差は、今回の2つのスラッシュコマンドで説明できる。3.2.1 の過去の5回中3回と原因が同じだったかは未確認。

一方、確認1の別セッションでは取り消し3入力に対して UserPromptSubmit=3、prompt=3、Stop=0。通常終了の12/12を、中断ターンにも必ず発火するという保証には使えない。

### 6. 未確認と限界

Windows／IDE／headless、ほかの Kiro バージョン、ほかのスラッシュコマンド、フックのタイムアウト・異常終了、同一コマンドを並行に繰り返す場合は未確認。背景実行は新規JUnitの1回を確認したもので、既存JUnitを持つ場合や、長期実行・失敗する背景処理は未確認。シェル本体の並行実行時間は測っていない。

一時ラッパーの実行時間が加わるため、無計測時のタイミングまで保証するものではない。ContextualHookInvoked の並びは観測したログの行順であり、コマンド実行の全時刻を表すと推測しない。今回もセッションログは文書化された形式とは扱わない。

公式の [Hook triggers](https://kiro.dev/docs/hooks/types/) では Stop は応答を終えた時点と説明される。[CLI Chat](https://kiro.dev/docs/cli/chat/) と [Built-in tools](https://kiro.dev/docs/reference/built-in-tools/) も確認したが、ここで観測した中断経路や背景実行の完了保証を補う根拠とはしていない。製品の修正、更新処理の変更、修正後の振り返りは今回行っていない。

リポジトリの `python3 -B -m unittest discover -s tests` は125件成功した。この自動テストの結果は、上記の Kiro 実機観測とは区別する。

### 7. 所見：決めたいこと1・2・5への影響

**1：閉じる時点。** 通常終了では Stop、次の通常発話では UserPromptSubmit が発火したため、両方を使い期限を保険にする案を支持する材料になる。ただし取り消し3件には Stop が無く、Stop 単独では覆えない。スラッシュコマンドも次の発話の代わりにはならない。さらに承認前取り消しのログでは turn_end の後に Post が記録されたため、ターン終端行だけを見て即座に「実行後フックなし」と断定すると誤る。遅れて来る Post との重複や競合も扱う必要がある。

**2：バックグラウンド。** 今回は PostToolUse の終了コード0が実処理の完了前に返り、開始記録もその時点で消費された。したがって「背景の開始記録を Stop では閉じない」だけでは足りない。起動時の Post をテスト完了として扱わない方式も必要になる。期限だけで扱う案は不明な状態を終わらせる保険にはなるが、後で生成されるJUnitを自動で取り込めるという意味ではない。

**5：二重計上。** policyDenial と selectedOption=reject の除外に加えて、同じ toolCallId の outcome=cancelled を除く必要がある。今回の承認前取り消しは既に tool_cancelled が記録されており、別の拒否として加算すると二重になる。実行中 Ctrl+C は failed であり、denied のフック停止とは別である。一方、フック行と呼び出しの一対一のキーは今回も得られなかった。イベント照合を加える場合も「同じセッション・同じコマンドが1件あれば一括で除外」では、複数回の実行を区別できない。ログの呼び出しIDによる明示的な拒否・取り消しの除外を優先し、確定できない対応は不足として残す必要がある。

以上は実機確認からの所見であり、方針の実装や問題の修正完了を示すものではない。

## 2026-11-15：Issue #4 の修正（自動テストのみ。実機は未検証）

上の2回の実機診断を受けて、次を実装した。フック入力とセッションログを模したテストで確かめたもので、**修正後の版を Kiro で動かしてはいない。**

### 変更

- **残った実行開始の記録を閉じる。** 記録にセッションを持たせ、同じセッションの `Stop` と次の `UserPromptSubmit` で閉じる。閉じたものは `recording_gap`（`test_end_missing`）として残す。`Stop` 用のフック `kiroku-record-turn-end` を足し、記録フックは5本になった。期限（`test_start_max_seconds`）は保険として残す。
- **実行後のフックが呼ばれなかったシェル実行を、集約のときにセッションログから拾う。** 状態が `denied` または `failed` で、応答に `Exit Code` の行が無く、`policyDenial` が無く、同じ `toolCallId` の `interaction_resolved` に `cancelled` も `reject` も無いものだけを数える。`denied` は「実行前に止められた呼び出し」、`failed` は「実行中に中断された呼び出し」として出す。止めたフックは断定せず、同じターンでそれまでに動いたフックの名前を候補として添える。表示だけで、「対象の型」には足していない。
- **承認画面の Deny once を、人による取り消しにする。** `The user rejected this tool call.` を判定の文言に足した。修正前に `recording_gap`（`unknown_not_run`）として記録されたイベントも、集約のときに読み直す。
- **バックグラウンドで起動したテスト実行を、完了として扱わない。** フック入力の `tool_input.run_in_background` が真なら、結果を読まず `background_not_finished` として残し、テスト実行の回数に数えない。あとで生成された JUnit は取り込まない。

### 根拠にした実測と、その限界

- 「応答に `Exit Code` が無い」ことと「実行後のフックが呼ばれない」ことは、実測した7つの場合（フックによる停止、実行中の Ctrl+C、権限の規則による拒否、Deny once、承認前の Escape、承認前の Ctrl+C、通常の失敗）で一致した。Kiro の仕様として文書化されたものではない。
- フック入力に `run_in_background` が入ることは、3.2.1 の初期検証（`tool_input` のキー一覧）に基づく。3.3.0 のフック入力で、真になることは確かめていない。入っていなければ、この修正は働かず、今までどおり `report_not_found` などとして出る。
- 取り消したターンでは `Stop` が呼ばれなかった（3回中0回）。そのため次の発話でも閉じる。次の発話が来ないままセッションが終わった場合は、期限で捨てる。
- 同じターンの複数の呼び出しでは、フックの記録と呼び出しを結ぶキーが無い。候補のフック名は、ターン単位でしか絞れない。

### 自動テスト

`python3 -B -m unittest discover -s tests`：138件成功（追加13件）。

### 既存の導入先への影響

`install --update` だけで済む。フックの定義ファイルに `Stop` が1本増える。`config.json`、イベント、ノート、台帳、レポートの読み方は変えていない。セッションを持たない古い実行開始の記録は、今までどおり期限で捨てる。

### 未検証

修正後の版での実機確認（依頼は Issue #4 のコメント）。Windows／IDE、headless、ほかの Kiro のバージョン、フックのタイムアウト、書き込みなどシェル以外のツールが止められた場合。

## 2026-11-15：修正版 4f30930 の実機検証

[Issue #4 の依頼](https://github.com/yuyumama/kiroku/issues/4#issuecomment-6889158181)の確認1〜6を実施した。直前の「自動テストのみ・実機未検証」は実装時点の記録であり、以下が修正後の実機結果である。期待と異なる出力・生成レポートは訂正せず保持した。今回の変更は検証文書だけで、配布コードと元のデモは変更していない。既存導入先への影響は「なし」。

### 1. 環境

- 対象：`fix/recording-and-retro-consistency` の `4f309302fde64bdf70e1f5c92ed02a4c332b6b5e`。更新前との比較には main `6519776` を使用。
- Ubuntu 22.04.4 LTS、Linux 6.8.0-52-generic、Kiro CLI 3.3.0、V3 対話。
- 対象版を一時領域に展開し、同じ版のデモとインストーラーで、基本動作・背景実行・同一ターンの混在・イベント無し・起動中の更新用の5コピーを作った。すべてリモート未設定の使い捨て Git。
- 複合コマンド `git push --force --dry-run ; python3 run_tests.py` は、各コピーの guard_shell.py へ標準入力で渡し、終了コード2を先に確認した。実際の push・削除、拒否の回避、`--trust-all-tools` は使用していない。
- 診断用の UserPromptSubmit／Stop フックと、実行前後の入力・state・JUnit を観測するラッパーは一時コピーだけに置いた。ラッパーは元の record.py に同じ入力を渡し、出力と終了コードをそのまま返した。更新試験では配布定義をそのまま使用した。
- 振り返りは配布版 kiroku-retro の定義・権限を変更せずに実行した。skill のロードと、そのセッションだけの読み取りを承認した。シェルの deny 規則は緩めていない。
- エージェントの完了申告ではなく、ログ、観測フック、state、イベント、集約、JUnit を照合した。ツール未呼び出しの完了申告は実行証拠に数えていない。生ログ、個別セッションID、発話本文、個人設定は転載しない。

対象版の `python3 -B -m unittest discover -s tests` は138件成功。以下の Kiro 実機観測とは区別する。

### 2. 確認1〜6の結果

| 確認 | 判定 | 結果 |
|---|---|---|
| 1：5本の読み込み・Stop で閉じる | 一致 | 開始記録0件、test_end_missing 1件、次の通常テストは11件成功 |
| 2：中断後の次の発話で閉じる | 一致 | 中断直後1件、次の発話後0件。次の通常テストは11件成功 |
| 3：集約の排他・回数・候補 | 部分不一致 | 6ケースの回数と除外は一致。候補に関係のない UserPromptSubmit フックが混入 |
| 4：背景テスト | 一致 | 入力の true を実測。background_not_finished で記録し、通常実行を妨げない |
| 5：起動中の更新 | 一致（旧形式は模擬入力による互換性確認を含む） | 再起動せず新しい Stop が動作。設定・データを保持、記録エラーなし |
| 6：振り返り | 部分不一致 | 検査・表示先・専用セッションの除外は一致。本文に未確認の原因断定と実測に合わない推測 |

#### 確認1：Stop による終了と、通常テストの復旧

`/hooks` で kiroku の記録フック5本（test-start、tool、file、prompt、turn-end）が読み込まれていることを確認した。デモの2本と診断用2本を含む一覧は9本だった。新しい kiroku-record-turn-end の実発火もログで確認した。

複合コマンドの実呼び出しは `status=denied / success=false / content="{}"`。PostToolUse は無かった。Pre のラッパーでは開始1件を確認し、そのターンの Stop 後には `test_starts={}`、追加イベントは `type=recording_gap / reason=test_end_missing` 1件だった。

続く通常の `python3 run_tests.py` は、終了コード0、`results.passed` 11件、failed／error／skipped 各0件。`note` は null ではなく**キー自体が無い**。ambiguous_test_run は出なかった。集約の summary に test_end_missing と「テスト実行の終わりが記録されなかった（別のフックによる停止、または中断の可能性）」の説明が出た。

#### 確認2：実行中の中断と、次の発話

一時コピーに、開始を診断記録へ書き、固定の30秒待機後に run_tests.py を呼ぶ `interrupt_run_tests.py` を置いた。名前は既存のテスト検出に一致する。Allow once の後、スクリプトの開始記録を実際に確認してから Ctrl+C を送った。

中断は `status=failed / success=false`、応答に Exit Code は無く、PostToolUse と Stop は無かった。中断直後の開始記録は1件。普通の発話を同じセッションへ送ると UserPromptSubmit で0件になり、test_end_missing が**追加で1件**残った。確認1と合わせた記録不足は2件であり、1回の中断を2回閉じたものではない。

次の通常テストも11件成功、note キー無しで取り込まれた。待機スクリプトの終了記録は生成されておらず、正常完了したものとは扱っていない。

#### 確認3：6ケースと、イベント無し・混在のセッション

基本動作の1つの導入先で、以下を実際に1回ずつ起こした。停止と中断には確認1・2の呼び出しを使用した。

| 起こしたこと・固定コマンド | 集約の節 | 件数 | 停止／中断への誤加算 |
|---|---|---:|---|
| フック停止：`git push --force --dry-run ; python3 run_tests.py` | 実行前に止められた呼び出し | 1 | 中断へは0 |
| 実行中 Ctrl+C：`python3 interrupt_run_tests.py` | 実行中に中断された呼び出し | 1 | 停止へは0 |
| agent-profile の deny：`printf KIROKU_PERMISSION_DENY` | 権限の規則による拒否 | 1 | 両方0 |
| Deny once：`printf KIROKU_DENY_ONCE` | 人による取り消し | 1 | 両方0 |
| 承認画面 Escape：`printf KIROKU_ESCAPE` | 人による取り消し | 1 | 両方0 |
| 通常失敗：`ls /no/such/kiroku-fixed` | ツール失敗 | 1 | 両方0 |

Deny once と Escape は両方とも `tool_cancelled` となり、unknown_not_run は出なかった。集約には取り消し2種類がそれぞれ1回ずつ出た。基本動作の集約は18イベント、通常テスト2回。終了しなかったテスト開始についての test_end_missing 2件は、停止・中断の表示とは別の「記録の不足」として出ている。

候補には guard-shell が含まれ、`kiroku-record-` のフックは含まれなかった。しかし **diagnostic-UserPromptSubmit も含まれた**。この観測フックは UserPromptSubmit で時刻を控えるだけのもので、PreToolUse ではない。候補に関係のないフックが出ないという期待には不一致だった。

別コピーでは kiroku の記録フック定義だけを外し、guard-shell と観測フックは残した。events.jsonl の存在しないセッションで停止の実呼び出し1件を起こすと、summary はイベント0件・ログのみ1セッション、tools は停止1回を表示した。「イベント無し」でも拾えることを確認したもので、Kiro のフック発火ログまで無いという意味ではない。

さらに別コピーの同一ターンで、停止する `git push --force --dry-run` と、独立した `printf KIROKU_MIXED_OK` を別々に実行した。turnId／executionId は同じで、前者だけ denied、後者は completed。PostToolUse は後者だけだった。集約は停止1回・中断0回で、正常な printf は追加されなかった。ここでも候補名の不一致は同じだった。

#### 確認4：バックグラウンド

一時スクリプト `diagnostic_run_tests.py` で、開始記録、固定の12秒待機、run_tests.py、終了記録を実行した。ログの args.run_in_background だけでなく、**PostToolUse の入力 tool_input.run_in_background が boolean の true** であることをラッパーで確認した。

実行後フックは処理完了前に来て、tool_response に Exit Code: 0 があった。その時点ではJUnitは未生成だった。記録は期待どおり、`type=test_run / results=null / note=background_not_finished / background=true`。開始記録は消費されて0件になり、Stop による test_end_missing は追加されなかった。

背景処理の終了後、本物のJUnitで11件・失敗0・エラー0を確認してから、通常の run_tests.py を実行した。こちらは11件成功・note キー無しで取り込まれた。集約は test_run イベント2件に対してテスト実行回数1回（通常分だけ）となり、背景分は「テスト結果の記録不足」に「バックグラウンドで起動」と表示された。

#### 確認5：旧mainからの更新

main `6519776` を導入した別コピーの Kiro を起動し、通常テスト1回の実記録を作った。保持検査用のノート1ファイルも固定の内容で用意した。**同じ Kiro セッションを開いたまま**修正版の `--update` を実行した。

更新されたのは次の6ファイル。新規0、更新6、変更なし9で、config.json は導入先の設定として保持された。

- .kiro/hooks/kiroku.json
- .kiro/skills/kiroku-retro/SKILL.md
- .kiro/skills/kiroku-retro/scripts/aggregate.py
- .kiro/skills/kiroku-retro/scripts/excerpt.py
- .kiro/skills/kiroku-retro/scripts/prompts.py
- .kiro/skills/kiroku-retro/scripts/record.py

インストーラーの実行前後で、config.json・events.jsonl・試験用ノート・state.json のハッシュはすべて一致した。更新後、同じセッションの /hooks に新しい Stop が現れた。複合コマンドの停止で開始記録0件・test_end_missing 1件となり、次の通常テストも11件成功・note キー無し。再起動は不要だった。

旧形式の確認では、更新後のコピーに**旧mainの record.py へ模擬 PreToolUse 入力を渡し**、session フィールドの無い開始記録を1件作った。その状態で、開いたままの実機セッションへ通常の発話を送り、新版の UserPromptSubmit／Stop を動かした。旧記録は1件のまま残り、record-errors.log は生成されなかった。旧記録はセッションで閉じず期限に任せるという実装と整合する。これは旧スクリプトが生成する形式の互換性確認であり、旧Kiroの異常終了を再現したものではない。

検証対象の更新影響は、今回の Linux／V3 対話・起動済みセッションでは「`--update` だけで済む」と確認した。

#### 確認6：専用エージェントでの振り返り

基本動作の導入先で、配布版 kiroku-retro を起動し `/kiroku-retro` を実行した。

- 最初の `aggregate.py --since 7d --require-agent`、facts の取得、保存後の `--check-report ... --require-agent` は、実際のツール結果で終了コード0を確認した。外からの再検査（require-agent 無し）も0。
- レポート `2026-11-15-1409.md`、ノート1件、提案2件を生成。停止・中断を「対象の型」には書かず、「記録の不足、確認事項」に各1回と固定コマンドを書いていた。
- ただし本文の別箇所に、未確認の停止原因の断定と、承認事実に合わない推測が残った。詳細は次節のとおり。生成物は訂正していない。
- レポート保存後、同じ振り返りセッションで固定の `printf KIROKU_RETRO_PERMISSION_DENY` を1回だけ要求し、agent-profile による実際の拒否を起こした。policyDenial があり、Exit Code: -1、tool_denied が記録された。拒否後の再試行はしていない。
- この診断の発話・拒否で生イベントは20件になったが、集約は振り返りセッションの2件を除外して18件のまま。停止1回・中断1回・開発時の権限拒否1回も増えなかった。再検査も0で、専用エージェント自身の拒否が混ざらないことを確認した。
- レポート・ノート以外の保全対象45ファイルは集合と内容が完全一致。台帳は作らず、採用・見送りも記録していない。

### 3. 不一致の詳細

| 対象 | 期待 | 実際 |
|---|---|---|
| 確認3：候補のフック名 | 関係のないフックが含まれない | 候補は guard-shell と diagnostic-UserPromptSubmit。後者は観測用 UserPromptSubmit であり、停止とは無関係 |
| 確認6：停止原因の説明 | 確認していない候補は原因と断定しない | 「記録の不足、確認事項」では未確認とした一方、「様子見」では「guard-shell による強制 push 停止」と断定した。ツールログに guard_shell.py 本文の読み取りは無かった |
| 確認6：実行中の中断 | 承認済みで開始したという実測と矛盾しない | 「利用者が承認しなかった可能性」と推測。実ログの interaction_resolved は selectedOption=accept で、診断スクリプトの開始記録もあった |

候補の混入は観測用フックを置いた条件で実測したもので、無計測のデモに同名のフックがあるという意味ではない。検査スクリプトが成功しても、本文中の原因説明や推測までは保証されなかった。以上は修正せず、そのまま結果として報告する。

### 4. 未確認と限界

Windows／IDE／headless、ほかの Kiro バージョン、フックのタイムアウト・プロセス異常終了、同一コマンドの並行実行、実行中のツール処理と同時に更新する場合は未確認。更新は Kiro を起動したまま、ターンが終わった状態で行った。旧形式の開始記録が実機の異常終了で残る過程と、3600秒経過後の実機動作は再現していない。

背景実行は1回の成功する処理を確認したもので、完了結果を後から自動取得する方式は対象外。シェル以外の停止、長期運用、提案採用後の効果は未確認。振り返り本文には上記の不一致が残るため、機械検査の成功をレポート全体の妥当性とは扱わない。

今回の対象は `4f30930` であり、その後の修正は含めない。配布コード・デモ・生成レポートを直して期待値に合わせることは行っていない。

## 2026-11-15：実機検証の不一致への対応（自動テストのみ。実機は未検証）

上の実機検証（修正版 `4f30930`）で見つかった不一致3件に、次のように対応した。

- **候補に関係の無いフックが混ざる（確認3）。** セッションログのフックの記録には、どのトリガーで動いたかが無い。フックの定義ファイル（プロジェクト内と、ユーザー共通）から名前とトリガーを引き、実行前（`PreToolUse`）以外と分かるフックを候補から外すようにした。定義が見つからないフックは、外さずに残す。実機で混ざった発話時の観測用フックの形を、テストに入れた。
- **レポート本文が、候補のフックを原因と断定した（確認6）。** 集約の表示と SKILL.md に、どの節でも断定しないことを書いた。
- **レポート本文が、実行中の中断を「承認しなかった可能性」と推測した（確認6）。** 集約の表示と SKILL.md に、実行が始まったあとの中断であり、承認されなかった実行とは別であることを書いた。

後の2件は LLM が書く本文の問題で、文面の変更で直ることは確かめていない。機械検査では防げないという限界は変わらない。

`python3 -B -m unittest discover -s tests`：139件成功。既存の導入先への影響は「`--update` だけで済む」。

未検証：この対応を入れた版での実機確認。エージェント定義の中に書かれたフックなど、定義ファイル以外で定義されたフックは「定義が見つからない」扱いで候補に残る。

## 2026-11-15：独立レビュー（Issue #7）への対応（自動テストのみ。実機は未検証）

`4f30930` への独立レビュー14件を確認した。13件は指摘どおりと判断して直し、1件（指摘3）は制約として文書に書いた。いずれもフック入力とログを模したテストで確かめたもので、対応後の版を Kiro で動かしてはいない。

| 指摘 | 対応 |
|---|---|
| 1. バックグラウンドの JUnit が、後続の通常実行の結果になる | 起動の記録を消さず、書き手が残っている印として残す。レポートが起動のときから変わるまでに始まった実行は `ambiguous_test_run`。変わったあとか、期限で印を消す |
| 2. レポートの指定が違うと、同じファイルへの並行実行を検出できない | 指定が同じファイルを指しうるかで判定する（`may_share_report`）。確かめられない組み合わせは、指しうるとする |
| 3. 閉じた実行の遅い実行後フックが、新しい実行の記録を消費する | **未対応（制約として README に記載）。** 閉じた履歴を持って保留にすると、中断したテストを同じコマンドでやり直すたびに保留になる。この順序が Kiro で起きることは、レビューでも未確認 |
| 4. 権限の規則が引用した文字列を、取り消しと誤認する | 応答の先頭の形を先に見る。権限の拒否を、取り消し・タイムアウトの文言より先に判定する |
| 5. `--update` で Python の起動名が変わる | 指定の無い更新では、導入済みのフックの起動名を引き継ぐ。`install.ps1` も、指定の無い更新では起動名を渡さない |
| 6. 権限の変更だけで、古い JUnit を新しい結果として取り込む | 比較から ctime を外した。ctime を含んでいた版の控えは、読むときにそろえる |
| 7. 発話が期間の外だと、期間内の停止・中断が消える | このリポジトリのセッションすべてを対象にする。期間の始まりより前から更新されていないログは読まない |
| 8. 振り返りを始める発話で閉じた不足が、振り返りの記録として除かれる | 不足に、実行が始まった時刻（`started_at`）を残し、集約はその時刻で扱う |
| 9. 同じ実行前記録が21件になると、不足が1件に減る | 1件の不足に回数（`count`）を持たせる。根拠の無い上限 20 は削除 |
| 10. 未知のログ形式を、黙って除外する | 判定できない呼び出しを「記録の不足」に `unreadable_tool_call` として出す。除外を正解としていたテストを直した |
| 11. glob の展開と、引用した同じ文字列が、同じ見出しになる | 引用されていない `*`、`?`、`[`、`{`、語頭の `~` を含む語は解釈しない |
| 12. 見出しの形が少し違うと、存在しない対象の型が検査を通る | レポートの検査が、台帳と同じ規則で案と対象の型を読む |
| 13. 新しい2節の回数が、検証値に無い | 検証値に `unrecorded_calls`（止められた回数、中断された回数）を足した |
| 14. ログを3回読み、不要な種別も保持する | 1回の実行の中では同じログを1度だけ読み、判定に使う種別だけを持つ |

レビューが「推測」とした、応答の文中の `Exit Code` で除外される件も、行としての終了コードだけを見るように直した。

### 対応していないこと、未検証

- 指摘3 の順序と、実行の途中で送った発話（Queue steering）で次の発話のフックが呼ばれるかは、実機で未確認。README の制約に書いた。
- `install.ps1` の変更は、PowerShell で実行していない。
- レポートを書かずに終わったバックグラウンドの実行のあとは、期限（最初の値 3600 秒）まで、同じレポートのテスト結果が保留になる。
- 対応後の版での実機確認。

`python3 -B -m unittest discover -s tests`：150件成功。既存の導入先への影響は「`--update` だけで済む」。起動名を `python`／`py -3` で導入した先も、指定なしの更新で起動名が保たれる。検証値に項目が増えたので、この版より前に保存したレポートを `--check-report` で検査し直すと不一致になる（検査は保存直後に行うものなので、手作業は要らない）。

## 2026-11-15：修正版 7a7fea5 の実機検証への対応（自動テストのみ。実機は未検証）

`7a7fea5` の実機検証の結果は、[Issue #4 のコメント](https://github.com/yuyumama/kiroku/issues/4#issuecomment-6893209543)にある（Kiro CLI 3.3.0、V3 対話、Linux）。指定した確認はすべて期待と一致し、追加のケースで不一致が1件見つかった。

### 実機で確かめられたこと（上のコメントの要約）

- バックグラウンド実行と通常の実行1回の重なりは保留になり、裏の処理が終わったあとの通常の実行は取り込まれた。`Stop` はバックグラウンドの記録を閉じなかった。
- 止めたフックの候補は `guard-shell` だけになった。発話時のフック、ファイル保存のフック、kiroku 自身のフックは出なかった。
- 取り消しの文言を引用する権限の規則は `tool_denied` になった。Deny once と Escape は `tool_cancelled` のまま。
- 実行の途中で送った発話：割り込み（Steer）では次の発話のフックは呼ばれなかった。キュー（Queue）では、実行後のフックと `Stop` のあとに呼ばれた。各1回の試行で、実行中のテストの記録が閉じられることはなかった。
- 指定なしの `--update` で Python の起動名が保たれた。期間の境界、振り返りを始める発話で閉じた不足、検証値の `unrecorded_calls` も期待どおり。

### 不一致と対応

**裏の処理が動いている間に通常の実行を2回行うと、2回目でバックグラウンドの記録が消えた。** 1回目の通常の実行が書いたレポートの変化を、裏の処理の完了とみなしていた。裏の処理はその約9秒後まで動いていた。

対応：通常の実行が終わった時点のレポートの状態をバックグラウンドの記録に控え、次の実行の前にそこから変わっているときだけ、裏の処理が書き終えたとみなす。実機の順序（裏の起動 → 通常の実行2回 → 裏の処理の書き込み → 通常の実行）を模したテストを足した。

### 残る制約と未検証

- 裏の処理と通常の実行が同時にレポートを書いた場合は区別できず、期限（最初の値 3600 秒）まで保留が続く。
- 振り返りの本文は、候補を原因と断定せず、中断を「承認されなかった」とも書かなかった。一方、中断の回数とコマンドを本文に書かなかった点、種類の違う不足を1文にまとめた点は残った。本文の問題で、機械検査の対象外。
- この対応を入れた版での実機確認。エージェント定義の中のフック、Windows／PowerShell、IDE、ほかの Kiro のバージョン。

`python3 -B -m unittest discover -s tests`：151件成功。既存の導入先への影響は「`--update` だけで済む」。

## 2026-11-15：b8f0f37 の実機での再確認

上の対応（バックグラウンドの記録を早く消さない）を、Kiro CLI 3.3.0、V3 対話、Linux で再確認した結果が [Issue #4 のコメント](https://github.com/yuyumama/kiroku/issues/4#issuecomment-6893787239)にある。3点とも期待と一致した。

- 裏の処理が動いている間の通常の実行2回は、どちらも `ambiguous_test_run`。2回目のあともバックグラウンドの記録が残った。裏の処理の終了は、通常の実行2回より後だった。
- 裏の処理が終わったあとの通常の実行は、11件成功として取り込まれ、バックグラウンドの記録は消えた。
- 重なりの無い場合（裏の処理が終わってから通常の実行）も、引き続き取り込まれた。

未確認：裏の処理と通常の実行が同時に JUnit を書く場合、期限まで保留が続く場合、複数のバックグラウンド実行、Windows／PowerShell、IDE。

## 2026-11-16：レポートの骨組みをスクリプトが作る（Issue #9 の段階1。自動テストのみ。実機は未検証）

振り返りのレポートは、検証値の照合に通っても、本文に誤りが残っていた（上の各実機検証の記録）。そのうち、集約が持っている事実を LLM が書き写す・言い換えるところで起きた誤りを、LLM に書かせないことで避ける。

### 調べたこと

- [Kiro の Agent Skills](https://kiro.dev/docs/skills/)：決定論的な作業（検証、ファイルの生成）にはスクリプトを使う、とある。テンプレートを埋める手順や、検証して直す手順の記述は無い。
- Claude Code の `/insights` の解説と、統計レポートの生成についての論文（Claim-Locked Reporting）：数値と構成はコードが組み立て、LLM は決まった枠だけを書く。形が崩れたら失敗させる。この2点を採り入れた。スクリプトから LLM を呼ぶこと、主張の強さまでコードが決めることは、採り入れていない。いずれも検索結果の要約に基づき、一次資料は読んでいない。

### 変更

- `aggregate.py --new-report` が、レポートの骨組みを `reports/` に作る。対象期間、件数、ハーネスの所在、記録の不足、前回の未判断の案の一覧、検証値は、スクリプトが書く。エージェントが書くのは「【ここに書く…】」の欄だけ。
- `--check-report` は、骨組みの目印があるレポートについて、スクリプトが書いた行が順番どおり残っていること、欄がすべて埋まっていること、表の「未確認」と冒頭の食い違いが無いことを確かめる。集約から骨組みを作り直して比べるので、行を消す、言い換える、まとめ直すと不一致になる。
- 欄が残っている書きかけの骨組みは、レポートとして数えない（最新のレポートにも、案の一覧にも入れない）。
- `SKILL.md` の手順6 を、骨組みを埋める手順に書き換えた。検証値を書き写す手順は無くなった。

### 自動テスト

`python3 -B -m unittest discover -s tests`：158件成功（追加7件）。エージェントが欄を埋める操作は、文字列の置き換えで模している。

### 既存の導入先への影響

`install --update` だけで済む。配布スクリプトが1つ増え（`skeleton.py`）、`SKILL.md` が変わる。以前のレポート、台帳、ノートは今までどおり読める。目印の無い以前のレポートは、以前と同じ規則で検査する。専用エージェントの権限規則は変えていない（骨組みを作るのは、すでに許可されている `aggregate.py` の実行）。

### 未検証

- Kiro の振り返りのエージェントが、骨組みの欄だけを書き換え、スクリプトの行を残すか。書き込みツールで部分的に書き換える操作が安定するか。ファイル全体を書き直した場合に、検査で落ちる頻度。
- 骨組みによって、解釈の誤り（[demo-cases.md](demo-cases.md) の D15 の h〜l）が増減するか。
- ハーネスのファイルが多い導入先で、表が長くなりすぎないか。
- Windows／IDE、headless、ほかの Kiro のバージョン。

## 2026-11-16：段階1 の実機比較と、その対応

`7731165` の実機検証の結果は、[Issue #9 のコメント](https://github.com/yuyumama/kiroku/issues/9#issuecomment-6911874038)にある（Kiro CLI 3.3.0、V3 対話、Linux。同じ記録に対して main 版と対象版の振り返りを各1回、更新後の振り返りを1回）。

### 実機で分かったこと（上のコメントの要約）

- **手順は動いた。** 骨組みの作成は拒否されず、エージェントは部分的な書き換え（`str_replace`）だけで21個の欄を埋めた。保存後の検査は、1回の振り返りでは初回で通り、更新後の振り返りでは3回目で通った（欄の目印を残したまま内容を書き足していた。検査が検出した）。
- **書き写し・言い換えの誤り（D15 の a〜f）は、main 版でも対象版でも出なかった。** 減ったことは、この比較では示せていない。各1回で、モデルの揺れと実装の効果を分けられない。
- **解釈の誤りは残った。** 応答の順序を実際と逆に説明する誤りが両方に出た。対象版では、開いたファイルの内容を誤って説明する箇所と、根拠の出どころ（ノートか、前回のレポートか）を取り違える箇所もあった。
- エージェントが、検証値の囲みを誤って消し、自分で読み直して戻した例があった。検査の前に戻したので、検査が捕まえた例ではない。
- 書きかけの骨組みがある間も、前回の未判断の案は未判断のまま出た。以前のレポートは、更新のあとも検査に通った。更新は `--update` だけで済んだ。
- 対象期間の表記が長く、検証値が96行でレポートの約半分を占めた。

### 対応（自動テストのみ）

- 対象期間を、分までの UTC の表記にした。正確な値は検証値にある。
- 検証値を1行にした。
- 欄の目印が残っているときの検査の文面と `SKILL.md` に、目印ごと置き換えることを書いた。実機で起きた2つの形（目印を残して書き足す、検証値の囲みを消す）が検査で落ちることを、テストに足した。

### 残っていること

- 段階1 の効果（書き写し・言い換えの誤りが減ること）は、実機では示せていない。骨組みの行が残ることは検査で保証されるが、以前の作りでその誤りがどの程度の頻度で出るかは、今回の1回では分からない。
- 解釈の誤りは、骨組みでは防げない。別に検討する（Issue #9 の段階3）。
- 両方の版で、ノートを保存したあとの再集約が、Kiro のシェルの実行から「サーバーを起動するコマンドに見える」という警告を受けて終了コード -1 になった。エージェントは実行の形を変えて再実行した。原因は未確認で、今回の変更によるものではない。

## 2026-11-16：主張に引用を付け、実在を確かめる（Issue #9 の段階3 の案A。自動テストのみ。実機は未検証）

段階1 の実機比較で、骨組みでは防げない誤りが残った。応答の順序を実際と逆に説明する、ファイルに無い記述をあるかのように書く、根拠の出どころを取り違える、の3つ。

### 調べたこと

- [Kiro の Tools](https://kiro.dev/docs/tools/)：V3 の読み取りは `read_file`、`list_directory`、`file_search`、`grep_search`、skill と steering の読み込みは `disclose_context`。入力の項目は書かれていない。[Session management](https://kiro.dev/docs/cli/chat/session-management/) に、保存の形式とツール呼び出しの有無は書かれていない。
- エージェントの申告は、モデルの外で記録されたツール呼び出しと照合する、LLM に見直させない、という指摘。主張に引用を付けさせて位置を確かめる方式（Anthropic の Citations）。引用があっても読み違いは起こる、という指摘。いずれも検索結果の要約に基づき、一次資料は読んでいない。

### 変更

- 型と改善案の中の主張に、`引用（利用者）`、`引用（応答）`、`引用（ファイル）` を書かせる。`--check-report` が、引用がそのセッションの発話、またはそのファイルに実在するかを確かめる（空白の違いは無視。ノートの引用と同じ照合）。
- 次の場合は不一致にする。照合できない引用がある。会話からの型に利用者の発話の引用が無い。原因を述べているのに応答またはファイルの引用が無い。既存のファイルを挙げているのに、そのファイルの引用が無い。
- 骨組みの目印を `kiroku-report:2` にした。引用の決まりは、この版のレポートだけに当てはめる。
- `SKILL.md` に、引用の決まりを足した。

実機で出た2つの誤り（言っていない応答の説明、ファイルに無い記述）の形が、検査で落ちることをテストに入れた。

### 限界

- 引用が実在しても、その解釈が正しいとは限らない。意図的な試験を型にする、候補を原因と断定する、といった誤りは、これでは防げない。
- 検査を通すために、誤った主張を残したまま引用だけを差し替える、という動きが出るかもしれない。実機で見る（[demo-cases.md](demo-cases.md) の D15 の m）。
- 書く量が増える。振り返りの時間とツール呼び出しへの影響は未測定。

### 未対応

- 表の「確認済み」を、振り返り自身のログの読み取りの記録と照合する（案B）。読み取りがログにどう残るかを実機で確かめてから実装する。

`python3 -B -m unittest discover -s tests`：173件成功。既存の導入先への影響は「`--update` だけで済む」。以前のレポートは今までどおり読める。

## 2026-11-17：案A の実機検証と、読み取りの記録の診断

`3c95ef3` の実機検証と、案B のための読み取りの記録の診断の結果は、[Issue #9 のコメント](https://github.com/yuyumama/kiroku/issues/9#issuecomment-6935478341)にある（Kiro CLI 3.3.0、V3 対話、Linux）。main 版の振り返りは、検査で欄の残りを検出した直後に実行環境の利用上限で止まった。完成版同士の比較にはなっていない。

### 案A で分かったこと（上のコメントの要約）

- エージェントは、利用者・応答・ファイルの引用を3件ずつ、計9件書いた。書く前に、抜粋、要約表示、`read_file` で該当箇所を得ていた。検査は初回で通った。不一致を受けたあとのエージェントの動きは、起きなかったので未確認。
- **引用が実在しても、説明の誤りは残った。** 応答の引用は、テストの成功という結論が先の、実際の応答だった。それでも、見出しと説明では「結論から始まらなかった」とした。引用と説明が並んでいても、食い違いは検査では捕まえられない。この方式の限界の実例。
- ファイルの内容の誤った説明（前回の `release.md` の例）は、案A の版では出なかった。main 版では同じ種類の誤りが出た。各1回で、引用の効果とは断定できない。
- 改善案の「対象の型」が案の内容と対応しないもの（テストのコマンドを直す案に、完了報告の話題の型）が、検査を通った。型と案の意味の対応は、機械検査では保証されない。
- 引用の決まりのために型や案を書かなくなった、原因をすべて「未確認」にした、という副作用は見られなかった。

### 読み取りの記録の形（同じコメントの要約）

| 読み取り | ログの形 |
|---|---|
| ファイル | `tool_call` の `toolName=read_file`、`args.path`（絶対パス）。範囲読みは `offset`、`limit`。成功時の `status` は `approved` で、成否は対応する `tool_result.success` |
| skill の読み込み | `toolName=load_resource`、`args.id`。`_meta.kiro.disclosedContext.uri` が file URI |
| 一覧、検索 | `list_directory`、`file_search`、`grep_search`。本文を読んだ証拠にはならない |
| 失敗 | 存在しないファイルは `status=failed`、権限による拒否は `status=denied`。どちらも `success=false` |
| 自動で文脈に入った資料 | `steering_inclusion` の `documents`（file URI の配列）。本文は、最初のターンの終わりに保存される `session_start` にある |

`KIRO_SESSION_ID` は、振り返りのセッションのログのディレクトリ名と一致した。

## 2026-11-17：「確認済み」を読み取りの記録と照合する（Issue #9 の段階3 の案B。自動テストのみ。実機は未検証）

上の診断で分かった形に合わせて、表の「確認済み」を、振り返り自身のセッションログと照合するようにした。

- 骨組みに、振り返りのセッション（`KIRO_SESSION_ID`）を書く。エージェントが書き換えて別のセッションを指せないよう、`data/state.json` にも控え、検査はそちらで骨組みを作り直して比べる。
- 「確認済み」としたファイルに、成功した `read_file`、`load_resource`、`steering_inclusion` のどれかが無ければ不一致にする。一覧と検索は数えない。
- 照合できないとき（セッションが分からない、ログを読めない、ログに今回の骨組みの作成がまだ記録されていない）は、不一致にせず、理由を表示する。

### 未検証

- **検査を振り返りの途中で実行したとき、そのターンの読み取りがログに書かれているか。** 診断では、`session_start` は最初のターンの終わりに保存されていた。`tool_call` が実行の直後に書かれるかは確かめていない。書かれていなければ、今回の骨組みの作成の記録も無いので、照合できないと表示されるだけになる（誤って不一致にはしない）。この場合、照合が働くのは、振り返りのあとに別の端末から検査したときだけになる。
- 長いファイルを範囲読みした場合に「確認済み」として扱ってよいか。いまは、範囲読みも本文を開いたと数える。
- エージェント定義の `resources` に `file://` で書かれた資料は、`session_start` の `composition.steeringFiles` にしか現れない。いまは数えていない。
- Windows のパス、IDE、ほかの Kiro のバージョン。

`python3 -B -m unittest discover -s tests`：186件成功。既存の導入先への影響は「`--update` だけで済む」。セッションの記録の無い骨組み（`kiroku-report:1` と、`KIRO_SESSION_ID` の無い環境で作ったもの）は、照合しない。

## 2026-11-17：案B（357d2a3）の確認

結果は [Issue #9 のコメント](https://github.com/yuyumama/kiroku/issues/9#issuecomment-6935964121)にある（Kiro CLI 3.3.0、V3 対話、Linux）。

- **新しい振り返りは、Kiro の利用上限で、ツールを1つも実行しないまま止まった。** 振り返りの途中で検査したときに、そのターンの読み取りがログに届いているかは、未確認のまま。
- 補助の確認として、前回（`3c95ef3`）の振り返りの実際のセッションログを、新しい照合の処理で読んだ。表で「確認済み」とされた7ファイルは、すべて本文を取得した記録と一致した。`status` が `approved` の成功した読み取りも数えられた。
- 同じレポートの診断用コピーで、開いていないファイルを1つ「確認済み」に書き換えると、検査は終了コード1になり、そのファイルについての不一致だけが出た。書き換えないコピーは終了コード0だった。
- これらは、終わったあとのログを読めることの確認で、振り返りの途中の検査が照合できることの確認ではない。照合できない場合は、検査は不一致にせず理由を表示する作りなので、誤って落とすことはない。

未確認のまま残すこと：振り返りの途中の検査での照合、骨組みを作るときに state に控えたセッションとの照合、照合で落ちたあとのエージェントの直し方。

## 2026-11-18：Issue #12 の不具合の修正（自動テストのみ。実機は未検証）

[Issue #12](https://github.com/yuyumama/kiroku/issues/12) の「不具合」のうち、原因がコードで分かる9件（指摘1〜8、10）を直した。どれも、Issue に書かれた再現の形をテストに入れ、直す前のコードでそのテストが落ちることを確かめた。指摘9（エクスポート先を設定したときの専用エージェントの確認）は Kiro の挙動に依存するので、調べてから扱う。

| 指摘 | 直したこと |
|---|---|
| 1 | `output_tail_chars` が 0 のとき、出力の全体を残していた（`[-0:]`）。0 は何も残さない。負数・数でない値は既定値で動き、`install --update` が知らせる |
| 2 | 記録の時点で応答の先頭から決めた権限拒否を、集約が切り詰めた末尾から決め直し、「理由不明」にしていた。新しい記録には、応答の全体で決めた印（`judged: "response"`）を付け、集約は決め直さない。印の無い古い記録は、これまでどおり決め直す |
| 3 | 同じレポートに同じ `P1` が2つあっても、検査と採用が通っていた。検査は番号の重複を不一致にする。台帳は、同じ ID が複数の案を指すときは記録せず、以前に記録された判断も、どの案のものかを推測して表示しない |
| 4 | 同じ分の `--new-report` が、目印の数が偶然そろうと、書き始めたレポートを上書きしていた。作り直すのは、同じセッションが作ったまま手を付けていない骨組み（作った時点の内容のハッシュが一致するもの）だけにした。ほかは秒まで（同じ秒なら `-2` 〜 `-9`）の名前で排他的に作る |
| 5 | PowerShell の `-Update` で起動名を指定しないと、`python` の無い環境でインストーラーまで届かなかった。指定が無いときは `python`、`py -3`、`python3` の順に、Python 3.9 以降として動くものを探す |
| 6 | 目印だけを消した空欄でも、検査を通り、最新のレポートになった。各欄の値、節の本文、表の列が空でないことを確かめ、同じ判定を台帳の「書きかけ」にも使う。骨組みを版3にし、節の中の2つの欄に見出しの行を付けた |
| 7 | 期間の後に書かれた継続方針のノートが、期間内の1回の指摘を型の候補にしていた。継続方針の例外は、期間内に照合できた継続方針があるときだけにした |
| 8 | 期間を広げても、前回の範囲の外の発話が既読のままだった。要約表示で見せた期間を state に控え、ノートを書く前に見せた範囲に入る発話だけを既読とする。壊れたノートも読み直しの対象にする |
| 10 | 見直し用の要約が、重なった3回の実行を1件と表示し、書きかけの骨組みをレポートに数えていた。集約・台帳と同じ数え方にした |

### 未検証

- **指摘5は、PowerShell で実行していない。** この環境には PowerShell が無く、入手したものの実行は許可されなかった。テストはスクリプトの文面を確かめるだけ。Windows PowerShell 5.1 が日本語の文字列を読めるよう、`install.ps1` と `uninstall.ps1` を BOM 付きの UTF-8 にした。これも未検証。
- 実機の Kiro での振り返り（骨組みの版3、別名での作成、読了範囲による未読の判定）は動かしていない。
- 指摘8の変更で、以前の版で書いたノートは「読んだ範囲の外に発話あり」になり、更新後の最初の振り返りで読み直しの対象になる。読む量は `retro_read_chars` で区切られる。実際の読み直しの量は未測定。

既存の導入先への影響は「`--update` だけで済む」。設定、記録、台帳、レポートの読み方は変わるが、手作業は要らない。版2のレポートは、ある行と節だけで空欄を見る。

## 2026-11-18：Issue #12 の改善と機能追加、指摘9（自動テストのみ。実機は未検証）

将来、人が介在しなくても動くことを目標に、人の申告や判断を前提にしない形で実装した。採用・見送りを人が決めることは「変えない設計」なので変えていない。

### 調べたこと

- [Steering](https://kiro.dev/docs/steering/#agentsmd)：`AGENTS.md` はリポジトリ直下、サブディレクトリ、`~/.kiro/steering/` で見つかり、inclusion モードに対応せず常に読み込まれる。入れ子の優先順位は書かれていない。
- [Permissions](https://kiro.dev/docs/permissions/)：シェルの規則の `*` は任意の文字列に当たり、引数を制限する仕組みは無い。[Headless mode](https://kiro.dev/docs/cli/headless/)：人がいない実行では、承認が要る操作はすべて拒否される。`KIRO_SESSION_ID` は、読んだ範囲の公式資料には無い（CLI で実測した値として使う）。
- [backpass](https://github.com/kunchenguid/backpass)（約1,500 stars）：適用した時点で `appliedAt` を記録し、適用の前に対象のファイルが測ったときの版のままかを確かめる。適用後の再発の増減は測っていない。作業量（セッション数）が足りないときに「測れない」と出す点を採り入れた。適用を人の承認に限る点、変更を自動で書く点は採り入れない（kiroku は人が反映する）。[pi-train-agents](https://github.com/ShuttleLab/pi-train-agents) も効果は測っていない。反映後の発生と作業量を数える実装は見つからなかった。

### 変更

| 項目 | 内容 |
|---|---|
| 改善1 | フックの記録が0件でも、未読のセッションがあれば振り返りで読む（SKILL.md の手順1）。`--prompts` も、集約のセッション一覧と同じセッションを対象にする |
| 改善2 | `doctor.py` が記録フック5本の名前、トリガー、`record.py` の呼び出しを確かめ、欠けたものを名前で出す |
| 改善3 | Kiro のセッションの中（`KIRO_SESSION_ID` がある）では、`aggregate.py`、`ledger.py`、`review.py` の `--root` を現在のリポジトリに限る。書き込み先は、すべて `--root` の下の `.kiro/kiroku/data/` |
| 改善4 | サブディレクトリの `AGENTS.md` を、steering の重さと比較表に入れる。git が追跡しているか、無視されていないファイルから探す |
| 機能追加 | 採用した案の反映の日を、案の「対象のファイル」がレポートの後に最初に変わった時刻（コミット、またはファイルの更新時刻）から決める。前後の比較の境目にし、反映後に観測できた作業量（テスト実行、記録のあるセッション、読み終えたセッション）を出す。観測が0なら「反映後の観測なし」 |
| 指摘9 | 実行中のセッションのログは、`sessions_dir` に無ければ Kiro の既定の置き場も探す。`--require-agent` が止まるときは、どの確認に失敗したかを出す |

### 未検証

- 実機の Kiro では動かしていない。サブディレクトリの `AGENTS.md` を Kiro が実際に読み込んでいるか、IDE で `KIRO_SESSION_ID` が渡るか（渡らなければ改善3の制限は効かない）、IDE で実行中のセッションのログがどこにあるか。
- 反映の日は、対象のファイルが変わったことしか示さない。案どおりの変更かは判定しない。レポートを作る前から未コミットだった変更を後でコミットすると、反映とみなす。
- 反映の日の判定は、レポート名（作った機械の時刻帯）を基準にする。別の時刻帯の機械で集約すると、ずれる。

`python3 -B -m unittest discover -s tests`：219件成功。既存の導入先への影響は「`--update` だけで済む」。台帳とレポートの形式は変えていない。以前のレポートの案（対象のファイルの行が無いもの）は、採用の時刻で比べる。

## 2026-11-18：PR #13 のレビュー指摘への対応（自動テストのみ）

[PR #13 のレビュー](https://github.com/yuyumama/kiroku/pull/13#pullrequestreview-5951289717)（Kiro CLI 3.4.0、V3、Linux 版 PowerShell 7.4.20 での確認）で出た4件を直した。

| 指摘 | 原因と変更 |
|---|---|
| 1（P1） | 以前の版で検査を通った版2のレポートが、更新しただけで不一致になった。版3で比較表に足した `AGENTS.md` の所在が、検証値の `harness` に入るため。以前の版のレポート（目印なし、版1、版2）は、その版の検証値（`AGENTS.md` の所在を除く）で比べ、版2は版2の骨組みを作り直して行も照合する。`0499158` のスクリプトで版2のレポートを作り、以前の版と今の版の両方で検査が通るテストを足した（git の履歴が無ければ飛ばす） |
| 2（P2） | 反映の日の基準を、レポート名の分の頭としていた。同じ分の、作る前のコミットを反映としていた。骨組みを作ったときの正確な時刻を `state.json`（`report_made`）に控え、それを基準にする。控えの無い以前のレポートは、名前の分（または秒）の終わりを基準にする |
| 3（P2） | 同じ番号の案が2つあるとき、`review.py` が1件の判断を「採用2件」と数え、集約も追跡していた。同じ ID が複数の案を指す判断は、採用・見送りの数と追跡から外し、件数だけを出す |
| 4（P2） | 明示した起動名が存在しても Python として動かないと、PowerShell が理由を出さずに終了コード1で終わった。明示した起動名にも、探すときと同じ起動確認をし、動かなければ名前と理由を出す |

あわせて、レビューの「既存の導入先への影響」にあった、引き継いだ起動名が環境から無くなっている場合について、`install.py --update` が注意と、更新し直すコマンドを表示するようにした。

未検証：PowerShell の変更（指摘4）は、この環境では実行していない。テストはスクリプトの文面を確かめるだけ。

## 2026-11-18：PR #13 の再レビュー指摘への対応（自動テストのみ）

[再レビュー](https://github.com/yuyumama/kiroku/pull/13#pullrequestreview-5952605703)（`079764a` を Kiro CLI 3.4.0、Linux 版 PowerShell 7.4.20 で確認）で出た3件を直した。

| 指摘 | 原因と変更 |
|---|---|
| 1（P1） | 旧形式のノートがあると、版2のレポートの再検査が更新だけで不一致になった。既読の判定を変えた（要約表示で見せた期間の控えが無いノートは読み直す）ので、検証値の未読の数が変わるため。以前の版のレポートの検査では、未読の数を版2までの判定（ノートを書いた後に発話が無ければ既読）で数え直す。振り返りで読むセッションは今の判定のまま。`0499158` で作った版2のレポートの回帰テストに、旧形式のノートを足した |
| 2（P2） | レポートを作ったのと同じ秒のコミットを、反映に数えていなかった。骨組みを作ったときの HEAD を `state.json`（`report_heads`）に控え、そこから辿れないコミットを作った後のものとする。HEAD の控えが無いときは時刻で比べ、同じ秒のコミットは「判別できない」と書く。時刻の控えも無い以前のレポートは、名前の分の終わりの時刻ちょうどのコミットも含める |
| 3（P2） | 更新時の注意が、実行ファイルがあるだけの、動かない Python を代わりの起動名として案内した。引き継いだ起動名も、案内する起動名も、実際に起動して Python 3.9 以降として動くかを確かめる。案内のコマンドの起動名を引用符で囲んだ（`py -3` が2つの引数に分かれないように） |

未検証：Kiro V3 での修正版のレポート完走（再レビューで headless の承認が得られず未確認）、Windows PowerShell 5.1、Kiro IDE。

## 2026-11-18：PR #13 の3回目のレビュー指摘への対応（自動テストのみ）

[3回目のレビュー](https://github.com/yuyumama/kiroku/pull/13#pullrequestreview-5953109725)（`6b73523` を確認。Kiro CLI 3.4.0 V3 で版3のレポートの作成・保存・検査の完走を確認済み）で出た3件を直した。

版2のレポートの再検査の回帰が3回続いたので、個別に直すのをやめ、「以前の版のレポート（目印なし、版1、版2）は、作ったときの版の規則で検査・判定する」と決めた。この PR の変更のうち、以前のレポートの検査・判定に効くものを洗い出し、すべて以前の規則に戻した。

| この PR の変更 | 以前のレポートへの効き方 | 対応 |
|---|---|---|
| 比較表の `AGENTS.md` の所在 | 検証値の `harness` | 前回対応済み。除く |
| 既読の判定（見せた期間） | 検証値の `unread`、骨組みの未読の行 | 前回対応済み。版2までの判定で数える |
| 継続方針の例外を期間内に限る（Issue の指摘7） | 検証値の `targets` | **今回の指摘1。** 版2までの判定（`candidate_v2`）で決め直し、版2のときの並びにそろえる（検証値の行は文字列で照合するので、並びも効く） |
| 空欄の検査（指摘6） | 検査の不一致、台帳の「書きかけ」（最新のレポートの判定と、前回の未判断の案の一覧） | 版3のレポートだけに当てはめる |
| 案の番号の重複の検査（指摘3） | 検査の不一致 | 版3のレポートだけに当てはめる。台帳が記録しないことは変えない |
| 振り返り自身のログの探し方（指摘9） | 「確認済み」の照合 | 版2までは `sessions_dir` だけで探す |

変えていないことも確かめた：記録の読み方（拒否の判定は、印の無い記録では以前と同じ）、テスト実行・ツール失敗・記録の不足の数、セッションの一覧、引用の照合。

そのほかの2件：

- 指摘2（P2）：HEAD の控えが無く、作ったのと同じ秒のコミットしか無いとき、それを反映の日にしていた。前後を判別できないコミットは反映の日に使わず、ほかに変更が無ければ「反映の前後を判別できない（判定を保留）」とする。
- 指摘3（P2）：更新時の注意が、POSIX では `install.sh`（`python3` で起動する）を案内していた。確かめた起動名で `install.py` を直接呼ぶコマンドを出す。テストで、案内どおりのコマンドが、`python3` の無い PATH で更新を終えることを確かめた。

`0499158` で版2のレポートを作る回帰テストに、期間の外の継続方針、番号の重複、目印を消しただけの空欄を足した。修正前のコードでは落ちる。

## 2026-11-18：sessions_dir 自体がセッションのディレクトリの配置（Issue #12 の5節。自動テストのみ）

PR #13 で対応が漏れていた。`sessions_dir` にセッションのディレクトリそのものを指定すると、`excerpt.find_log` は読めるのに、`prompts.discover` は子と孫だけを探していたため、集約のセッション一覧に出なかった。`sessions_dir` の直下の `session.json` も、同じ作業場所の照合に通すようにした。既存の導入先への影響は「`--update` だけで済む」。

## 2026-11-18：一歩先の改善案とハーネスの診断（自動テストのみ。実機は未検証）

利用者の指摘（案が事例を数えるだけで、何が価値になるかの視点が無い）を受けて、[proposal-design.md](proposal-design.md) の調査と設計案 B を実装した。

- `scripts/diagnose.py`：ファイルだけから決まる診断値。D1（読み込みの条件に当たるファイルが無い steering）、D2（参照先の無いパス・`make` の target・`npm run` の script・hook とエージェントが呼ぶスクリプト）、D3（同じ文）、D5（事後の検査の一覧）、D6（型ごとの負担）。
- 骨組みを版4にした。「一歩先の改善案」の節を冒頭に置き、比較表と診断値を後ろに回した。検証値に `diagnostics`（手がかりに書ける診断値の名前）を足した。
- 検査：一歩先の案の件数（`one_step_max`、既定3）、観点（V1〜V8）、段（観測の段→案の段。上がっていること）、手がかり（集約の型か診断値）、確かめ方。
- 以前の版のレポート（版3まで）は、作ったときの規則で検査する。版3は、検証値から `diagnostics` を除き、版3の骨組みを作り直して照合する。`89731e1`（版3を作っていた main）のスクリプトで版3のレポートを作り、旧版と新版の両方で検査が通るテストを足した。
- 家計簿のデモのファイルで、`D1:.kiro/steering/python-style.md` と `D2:.kiro/steering/testing.md:make test` が、記録なしで出ることをテストで確かめた。gitignore されている `data/kakeibo.json` は数えない。

### 未検証

- 実機の Kiro での振り返り。LLM が一歩先の案を、観点を形だけ埋めずに書けるか。D17〜D21 の結果で見る。
- `fileMatchPattern` の照合が Kiro と同じか（kiroku は、ファイル名だけのパターンをどの階層でも当たるとみなす。広めに当てて、読み込まれないと誤って言わない側に倒した）。
- D2 が、実際の利用者の steering で誤検出をどれだけ出すか。

既存の導入先への影響は「`--update` だけで済む」。設定、記録、台帳の形式は変えていない。以前の版のレポートは、作ったときの規則のまま検査する。`config.json` に `one_step_max` を書く必要は無い（既定値で動く）。

## 2026-11-19：記録をやり直す reset.py（自動テストのみ）

利用者の依頼で、記録をゼロからやり直すコマンドを足した。`data/` の中身を `data/backups/<日時>/` に移し（消さない）、新しい state に「記録の始まり」を控える。集約と要約表示は、その時刻より前を数えない。Kiro のセッションログは残るため、期間で切らないと、やり直す前の会話が未読として戻るからである。専用エージェントの許可規則には入れていない（テストで確かめた）。既存の導入先への影響は「`--update` だけで済む」。

## 2026-11-19：コードの判定の見直し（公式ドキュメントと配布物のソース。実機ではない）

D2 の誤検出（モノレポ）と、vitest のテスト失敗が「テストファイルは変更なし」の回帰候補になった報告を受けて、スクリプトの判定を全部見直した。経緯と判断は [judgment-review.md](judgment-review.md) にある。

### 確かめたこと

- Kiro の steering のファイル参照：[Steering](https://kiro.dev/docs/steering/)（2026-10-21 更新）の File references。`#[[file:<相対パス>]]`、`#[[file:<相対パス>:<始め>-<終わり>]]`、`#[[folder:<相対パス>]]`。基準は、ワークスペースの steering がルート、ユーザー共通の steering が `~/.kiro/steering/`、AGENTS.md がそのフォルダ。D2 はこの基準で確かめる。
- JUnit の形：配布物のソースを読んだ。vitest 3.2.4 は、既定で `file` 属性を出さず、classname が vitest の root からのパス。jest-junit 16.0.0 は、既定で `file` 属性を出さず、classname が `{classname} {title}`。pytest 8.3.3 は、既定の `junit_family=xunit2` で `file` 属性を出さず、classname がドットでつないだモジュール名。
- 以前の分類は、`file` 属性と Python のモジュール名の形しか想定していなかった。実機で確かめていたのは、`file` 属性を出すデモのランナー（`examples/demo/run_tests.py`）だけである。

### 自動テストで確かめたこと

- D2：Kiro のファイル参照と Markdown のリンクの参照切れを断定する。文章の中のコードの語は、行の文面を添えた候補にする。報告の事例（`> file.json`、`.opt-in.md`、`.test.ts`）は出ない。`specs/AC`、`test_xxx.py` は候補として出る。
- テスト失敗：vitest の形（`frontend/` の中から実行）で、テストファイルの書き換えを Red にする。jest の形と、同じパスのテストが2つのパッケージにあって片方だけ変わった場合は「特定できず」。一部だけを流した実行の後の失敗を、Red と分ける。新しいテストは、修正前のコードで失敗することを確かめた。
- 手直し候補：シェルで書き換えたファイルに、書き込みの後のシェル実行の1行目を手がかりとして添える。
- 要約表示：失敗したシェル実行の見出しと、テストに一致した実行の終了コードを出す。

### 未検証

- 実機の振り返りで、エージェントが D2 の候補を読み分けるか。
- 実際に vitest・jest・pytest を実行して作った JUnit での分類（ソースを読んだだけ）。go の形。
- Kiro が、コードの中に書かれた `#[[file:…]]` を解決するか。

既存の導入先への影響は「`--update` だけで済む」。設定、記録、ノート、台帳の形式は変えていない。

## 2026-11-19：PR #17 の実機レビュー（#18）を受けた修正

Kiro CLI 3.4.0 V3（Ubuntu 22.04）で、家計簿デモに `8b0707b` を導入した実機レビューの結果を受けた。

- 実機で確かめられたこと（レビュー）：D5 の値（書き込み4ターン中3ターン）と全体のテスト実行数は実ログと一致した。テスト失敗の分類の根拠3行は正しかった（unittest の FunctionTestCase は classname と file が標準ライブラリを指すので「特定できず」）。`resources` の無いカスタムエージェントにも steering が読み込まれた（手引きの記述どおり）。
- 直したこと（自動テストのみ）：承認されずに終わった呼び出し（終了コード -1、本文に `this call did not run`）を、要約表示がテストの実行に数えていた。実行には数えず、件数を別に出す。hook のコマンドの相対パスを、別の場所の同じ名前のファイルの末尾一致で「有る」としていた。hook とエージェント定義はルートからだけ解決する。
- 見送ったこと：`#[[folder:…]]` がファイルを指すなど、参照の種類の取り違えの検出。

## 2026-11-20：振り返りでの質問と、デバッグ欄の設計のための確認（公式資料と自動の再現。実機ではない）

[questions-and-debug-design.md](questions-and-debug-design.md) の設計のために確かめた。まだ実装していない。

### 公式資料で確かめたこと

- [Built-in tools](https://kiro.dev/docs/tools/)（2026-11-12 更新）の一覧に、利用者に質問するツールは無い。`AskUserQuestion` が出てくるのは、Kiro Crew のチャットの資料（[Message controls](https://kiro.dev/docs/crew/chat/message-controls/)）だけだった。
- [Plan mode](https://kiro.dev/docs/specs/plan/) は、番号つきの質問と選択肢を普通のチャットの文章で出し、「1=a, 2=b」か自分の答えで答えさせる。一部だけ答える、自由に書く、飛ばすこともできる。
- skill の名前の後ろの文字は、CLI でも IDE でも、エージェントに追加の文脈として渡る（[Agent Skills](https://kiro.dev/docs/skills/)、[IDE の Slash commands](https://kiro.dev/docs/ide/chat/slash-commands/)）。本文の `$ARGUMENTS` と `${N}` の置き換えは CLI だけ。リンク先の「Passing arguments to a skill」の節は、ページに無かった。上の4節（3.2.1 の実機）で、後ろの文字が渡ることは確かめている。
- headless では、途中の入力ができない（[Headless mode](https://kiro.dev/docs/cli/headless/)）。
- MCP のツールは、途中でフォームを出して入力を求められる（elicitation）。CLI はローカルの V3 セッションで 2.34 から（[MCP の Tools](https://kiro.dev/docs/mcp/usage/)）。IDE 1.x と headless での扱いは書かれていない。

### 自動で再現したこと

骨組みを作った後に `record-errors.log` が増えると、`--check-report` が、スクリプトが書いた「記録フックのエラー：N 行」の行を不一致にする。一時ディレクトリで、増やす前は終了コード0、1行足した後は終了コード1になることを確かめた。実機では見ていない。直し方は設計書の9節にある。

### 未検証

Kiro CLI 3.4.0 と IDE で `/kiroku-retro --debug` の後ろの文字が渡るか。IDE で振り返り自身のログを読めるか。Kiro の版をログから取れるか。エージェントが質問の後でターンを終えて待つか。MCP の elicitation の実際の表示と、エージェントが答えを受け取る形。

既存の導入先への影響は「なし」（文書だけの変更）。

## 2026-11-20：記録フックのエラーの行数で、検査が不一致になる不具合の修正（自動テストのみ）

上の節で再現した不具合を直した。

- 骨組みを作ったときの `record-errors.log` の行数を、`data/state.json` の `report_record_errors` に控える。検査は、その数で骨組みを作り直す。エージェントは state に書き込めない（書き込みの許可は `reports/**` と `notes/**` だけ）。
- 控えの無い以前のレポートは、作った時刻（`report_made`）までの行を数える。行の先頭の時刻（`record.py` が書く UTC）で区切り、時刻の無い行は直前の行と同じ件とする。`report_made` も無いものは、今までどおり検査のときの行数で比べる。
- 集約の通常の出力は、今までどおり今の行数を出す。
- テスト：骨組みの後に1行増えても検査が通ること。控えの無いレポートが、作った時刻までの行（続きの行を含む）で比べること。2つとも、直す前のコードで落ちることを確かめた。

未検証：実機の振り返りの最中に、記録フックがエラーを出す場面は見ていない。

既存の導入先への影響は「`--update` だけで済む」。state に項目を1つ足すが、以前の版のスクリプトはこの項目を読まず、state を保存するときもそのまま残す。設定、記録、ノート、台帳の形式は変えていない。

## 2026-11-20：振り返りでの質問（段1）の実装（自動テストのみ。実機は未検証）

[questions-and-debug-design.md](questions-and-debug-design.md) の段1を実装した。

### 変更

- skill に手順5「利用者に確かめる」を足した（以降の手順の番号は1つずつ下がる）。聞き方は grilling（mattpocock/skills）にならう。
- 骨組みを版5にし、冒頭の要約の次に「利用者に確かめたこと」の節を置いた。
- `answers.py`：節を読み、答えの引用（`引用（回答）`）が、振り返り自身のセッションの、集約を始めた後の利用者の発話にあるかを確かめる。質問の数の上限、番号の重複、手がかりが集約にあること、反映に書いた案がレポートにあることも確かめる。
- 集約に「利用者に聞く質問の上限」と「前回までの回答」を出す。
- 設定 `question_max`（既定3。最初の値。0 で聞かない）。
- 版ごとの検査の規則を、版の番号で固定した（案の番号の重複の検査と、今の探し方での「確認済み」の照合は、版4から）。版を上げても、版4のレポートの扱いは変わらない。

### 自動テストで確かめたこと

- 利用者が書いた答えは通る。書いていない答え、振り返りの前の発話、エージェントの応答は通らない。4文字未満の答えは、発話の全体と一致するときだけ通る。
- 未回答には引用が要らず、答えには引用が要る。上限（1 と 0）、番号の重複、手がかり、反映に書いた案を確かめる。
- ログを読めないとき、ログが骨組みの作成まで届いていないときは、照合できないと表示し、不一致にしない。
- 次の集約が前回の答えを出す。書きかけの骨組みは読まない。台帳は、質問（Q）を案として読まない。
- 版4（`647e60c`）で作って検査を通ったレポートが、更新した後も通る。

### 未検証

- 実機（Kiro CLI 3.4.0、IDE）で、エージェントが質問の後にターンを終えて待つか。質問の文面が、プログラマーではない利用者に分かるか。見立てが答えを誘導しすぎないか。
- `/reply` で答えたときのログの形。
- IDE で、振り返り自身のログを読めるか（読めなければ、答えは照合できないと表示される）。

### 気づいたこと（動きは変えていない）

README は、案の番号の重複の検査を「版3のレポートだけに当てはめる」と書いていた。しかし、骨組みを版4に上げたとき（PR #14）から、版3のレポートには当てはめていなかった（`numbers=form >= skeleton.VERSION` だったため）。今回は動きを変えず、README を実際の動きに合わせた。

既存の導入先への影響は「`--update` だけで済む」。更新した後の振り返りは、途中で質問をしてターンを終える。望まない導入先は `question_max` を 0 にする。設定、記録、ノート、台帳の形式は変えていない。

## 2026-11-20：kiroku の不具合の候補の欄（段2、`--debug`）の実装（自動テストのみ。実機は未検証）

[questions-and-debug-design.md](questions-and-debug-design.md) の段2を実装した。

### 変更

- `/kiroku-retro --debug` のとき、エージェントは骨組みを `aggregate.py --new-report --debug` で作る（`SKILL.md`）。シェルの許可は `aggregate.py *` なので、権限は変えていない。
- `debug.py`（新規）：環境（OS、Python、骨組みの版、`scripts/*.py` の指紋）、振り返り自身のログで骨組みの作成より前に失敗したシェル実行（kiroku のスクリプトの失敗と、権限の規則による拒否）、記録フックのエラーの数と種類と最後の行を作る。骨組みを作った時点で `data/state.json` の `report_debug` に控え、検査は控えから同じ文面を作り直す。
- エージェントが書く `### B1. …`（現象、期待、根拠、引用、再現の手がかり、確度、影響）の形と、`引用（出力）` が振り返りのツールの出力に実在することを確かめる。
- 指紋は改行の違いをそろえてから計算する（インストーラーは Windows で CRLF に書くため）。`debug.py --fingerprint` で、kiroku のリポジトリの版の指紋を出せる。

### 自動テストで確かめたこと

- 指定しないときは節ができない。手を付けていない骨組みを指定なしで作り直すと、節と控えが消える。
- 失敗したシェル実行の一覧に、kiroku のスクリプトの失敗（Traceback つき）と、権限の規則による拒否が出る。kiroku 以外の失敗と、骨組みの作成より後の実行は出ない。
- スクリプトが書いた行を変えると不一致になる。骨組みの後に記録フックのエラーが増えても、控えで比べるので一致する。控えが無いときは行を照合せず、そう表示する。
- B の欄：出力に無い引用、確認済みなのに引用が無い、欄が無い、確度の書き方、短い引用、番号の重複、見出しが無いものは不一致。ファイルの引用は、これまでの引用の検査が確かめる。
- ログが骨組みの作成まで届いていない、ログが無いときは、照合できないと表示し、不一致にしない。

### 未検証

- Kiro CLI 3.4.0 と IDE で、`/kiroku-retro --debug` の後ろの文字がエージェントに渡り、エージェントが `--debug` を付けて骨組みを作るか（3.2.1 では、後ろの文字が渡ることを確かめている）。
- 実機のログで、失敗したシェル実行のツール結果の形（`Exit Code:` の行、拒否の文言）が、テストで模した形と同じか。
- Kiro の版を、ログか `session.json` から取れるか（今は取らず「未確認」と書く）。
- `sessions_dir` を設定した導入先で、エージェントが振り返り自身のログを抜粋で開けるか（抜粋は `sessions_dir` だけを探す）。

既存の導入先への影響は「`--update` だけで済む」。指定しなければ、レポートは段1と同じ。state に項目を1つ足すが、以前の版のスクリプトはこの項目を読まない。設定、記録、ノート、台帳の形式は変えていない。

## 2026-11-20：ハーネスの地図と、承認からの権限の提案の設計のための確認（公式資料。実機ではない）

[best-practices-design.md](best-practices-design.md)（旧 harness-map-design.md）の設計のために、公式資料で次を確かめた。まだ実装していない。

- 権限（[Permissions](https://kiro.dev/docs/permissions/)、2026-11-18 更新）：シェルの規則の `*` は任意の文字の並びで、`**`・`?` は使えない。つないだコマンドは分けて1つずつ判定する。優先は deny > ask > allow > evaluate で、範囲の間に優先は無い。ワークスペースの規則は `~/.kiro/workspace-roots/<hash>/permissions.yaml`（リポジトリの外）、ユーザー共通は `~/.kiro/settings/permissions.yaml`。承認画面の Always allow は、段（完全一致、2語＋`*`、1語＋`*`）と範囲を選んで規則を保存する（CLI 2.36 から）。
- Approve for me：規則で決まらない操作を LLM の判定で通す・止める・聞く。既定では切ってある。有料の利用者向け。「セキュリティの仕組みの代わりにはならない」。
- PermissionRequest のフック（[Hooks のトリガー](https://kiro.dev/docs/hooks/types/)、CLI 3.4.0 から）：承認画面が出る直前に動き、規則で決まった呼び出しでは動かない。
- memories（[Memory](https://kiro.dev/docs/memory/)）：利用者ごとにローカルに保存。「一貫して守らせたい決まりは Steering に書く」。保存場所の記載は無い。

未検証：承認画面で Allow と Always allow を選んだときの、ログの `interaction_resolved` の `selectedOption` の値。既定の権限で、読み取りだけのコマンドに承認画面が出るか。

既存の導入先への影響は「なし」（文書だけの変更）。

## 2026-11-24：D5 のコメントの除外、期間の既定（`--since last`）、件数の上限の撤廃（自動テストと比較のみ。実機は未検証）

[best-practices-design.md](best-practices-design.md) の段a（4節）と、共通の変更（3.2節、3.3節）を実装した。

### 変更

- `D5` の CI：照合の前にコメントを除く。YAML は行頭と空白の後の `#` 以降（引用符の外）、Jenkinsfile は `//` 以降。実際のコマンドの後ろのコメントは除くだけで、行は残る。
- 集約に `--since last` を足し、手順（`SKILL.md`）と集約のコマンドの既定にした。書き終えた最新のレポートの検証値の `until` から読む。そのレポートの `unread` が 0 でなければ、その `since` から読む。書き終えたレポートが無ければ、すべて（`reset.py` の後は記録の始まりから）。書きかけの骨組みは数えない。
- `one_step_max` と `question_max` の既定を `null`（上限なし）にした。整数を書けばその数まで、0 なら出さない（以前の意味のまま）。インストーラーの「既定値で動く項目」は `null（上限なし）` と表示する。
- 質問の手順：件数を絞らず、答えで変わる案が大きい順に並べ、冒頭に「上から重要な順です。全部に答えなくて構いません」と書く。

### 自動テストで確かめたこと

- コメントだけの CI は「一致なし」。コマンドの後ろのコメントは「一致あり」。引用符の中の `#` は除かない（`echo "#pytest"` は一致あり）。Jenkinsfile の `// sh 'pytest -q'` は「一致なし」。直す前のコードでは、コメントだけの CI が「一致あり」になって落ちる。
- `--since last`：最新の書き終えたレポートの `until` から。書きかけは数えない。未読が残っていたら `since` から。前回が「すべて」から読み、未読が残っていたら、すべて。コマンドの期間の表示と、新しい骨組みの検証値の `since` が、前回の `until` になる。
- 上限なしの既定で、5問の質問を1問ずつ照合する（5問目の引用が無ければ不一致）。上限なしで、一歩先の案を何件でも書ける。整数（1、2）と 0 は、以前の意味のまま。

### 今の版との比較（スクリプトで再現。LLM は動かしていない）

デモのコピーに、コメントにだけテストのコマンドがある CI と、3つのセッション（25日前、15日前、2日前）、21日前に終わった振り返りのレポートを置き、今の版（`e1f7312`）は手順の既定 `--since 7d`、新しい版は `--since last` で集約した。

| ケース | 今の版 | 新しい版 |
|---|---|---|
| D28（CI のコメントにだけテストのコマンド） | 「match に一致する行がある」（誤判定） | 「match に一致する行が無い」 |
| D29（前回の振り返りから3週間） | 期間は直近7日。15日前のセッションは未読に出ない | 期間は前回のレポートの終わりから。15日前と2日前のセッションが未読に並ぶ。25日前（前回の期間）は出ない |

### 未検証

- 実機（Kiro CLI 3.4.0、IDE）で、エージェントが `--since last` を使い、利用者が期間を書いたときに `--since 14d` のように置き換えるか。
- 件数を絞らないとき、質問と一歩先の案が実際に何件になり、利用者が読み切れるか。

既存の導入先への影響は「`--update` だけで済む」。振る舞いは変わる：期間の既定が前回の振り返りの後からになり、件数の既定が上限なしになる（`config.json` に数を書いていれば、その数を守る）。設定、記録、ノート、台帳、レポートの形式は変えていない。

## 2026-11-24：手作業の確認から合格条件を聞き出す（骨組みの版6。自動テストと比較のみ。実機は未検証）

[best-practices-design.md](best-practices-design.md) の段b（5節）を実装した。

### 変更

- ノートの種類に `manual_check`（手作業の確認）を足した。利用者が自分で確かめた・これから確かめる・目で見た、と述べた発話や、エージェントに自分の確認を待たせた発話を、振り返りのエージェントが意味で拾う（語では分類しない）。引用の照合は、ほかの種類と同じ。
- 期間内に手作業の確認があるトピックは、セッションの数にかかわらず、会話からの型の候補（対象の型 `topic:…`）にする。集約の review の節に「手作業の確認」の一覧（トピック、セッション数、回数、例の引用）を出す。
- 骨組みを版6にした。一歩先の改善案と質問の手がかりに、比べたハーネスの表のファイル（kiroku 自身のファイルを除く）を書ける。一歩先の改善案では、そのファイルからの引用（`引用（ファイル）`）を、その案の中に要る。表に無いファイル（README など）は手がかりにならない。
- 版5までのレポートは、手作業の確認を数えないトピック（`topics_v5`）で検証値と骨組みを作り直して比べる。手がかりも、作ったときの規則のまま。
- 質問の雛形（「何が一致すれば、確認済みにできますか。人が見たい例外はありますか？」）を `references/harness-guide.md` に置き、手順5のきっかけの表に足した。

### 自動テストで確かめたこと

- 1セッション1回の手作業の確認が、候補と対象の型になり、「手作業の確認」の一覧に、セッション数・回数・例の引用が出る。期間の外の手作業の確認は、候補にしない。無ければ「なし」。
- 版5の判定（`topics_v5`、`as_version(data, 5)`）は、手作業の確認を数えない。
- 版5（`3edb683`）で作って検査を通ったレポートが、更新した後も通る。更新の後に、同じ期間のノートへ手作業の確認を書き足しても通る。この判定を外すと、検証値の不一致で落ちることを確かめた。
- 一歩先の改善案：表のファイルを手がかりにし、そのファイルからの引用があれば通る。引用が無い、引用がファイルに無い、表に無いファイル（README）、kiroku 自身のファイルは不一致。版5の印のレポートでは、ファイルは手がかりにならない。
- 質問：表のファイル（skill の SKILL.md）を手がかりにした質問が通る。版5の印のレポートでは通らない。手作業の確認のトピックを手がかりにした質問が通り、そのトピックが検証値の対象の型と、型ごとの負担（D6）に出る。

### 今の版との比較（スクリプトで再現。LLM は動かしていない）

デモのコピーに、セッションC（demo-cases.md）と同じ2つの発話のログと、そのノートを置き、集約の review の節を比べた。

| 条件 | 今の版（`3edb683`） | 新しい版 |
|---|---|---|
| ノートを、今の版にある種類で書く（いちばん近い `unsure`） | 「まだ型ではないトピック」（1セッション）。候補にならず、質問・案の手がかりにならない | — |
| ノートを `manual_check` で書く | 「ノートの検査で外したもの」（kind が不正）。数えられない | 候補（1セッション、2回、手作業の確認）と、「手作業の確認」の一覧に出る |

### 未検証

- 実機で、エージェントが手作業の確認を意味で拾えるか（語だけで拾わないか、拾い漏らさないか）。
- 合格条件の質問が、プログラマーではない利用者に分かるか。答えから、照合を自動にする案（V3）が出るか。
- 文書に書かれた人の確認を、手順4で拾えるか。

既存の導入先への影響は「`--update` だけで済む」。ノートの種類が1つ増え、骨組みの版が6になる。以前のノートとレポートは、そのまま読める。以前の版のレポートは、作ったときの版の規則で検査する。設定、記録、台帳の形式は変えていない。

## 2026-11-24：ベストプラクティス集との照らし合わせ（骨組みの版7。自動テストと比較のみ。実機は未検証）

[best-practices-design.md](best-practices-design.md) の段c（6節）を実装した。

### 出典の読み直し（2026-11-24、公式資料と公開ページ）

集の各項目の出典を読み直し、確認日を付けた。11 ページすべて読めた。

- [Kiro, Steering](https://kiro.dev/docs/steering/)（2026-10-21 更新）：CLI V3 は always、fileMatch、manual、auto。AGENTS.md は条件を持たず、常に読み込まれる。1ファイルに1領域、理由を書く、秘密を書かない。
- [Kiro, Hooks のトリガー](https://kiro.dev/docs/hooks/types/)（2026-11-18 更新）：Agent Stop の用途に「code compilation, testing, formatting」、File Save に「Run linting and formatting」、Session Start に「Inject workspace-specific instructions into the agent's context」。Post Task Execution もある。
- [Kiro, Permissions](https://kiro.dev/docs/permissions/)（2026-11-18 更新）：capability の一覧、deny > ask > allow > evaluate、エージェント定義の `permissions`。deny の例は `rm -rf *`、`sudo *`、読み取りの `**/.env`、`**/.env.*`、`**/*.pem`。`git push --force` の例は無い（例のコマンドに入れたのは、claude-elixir-phoenix の分け方による。2026-11-20 に確認）。
- [Kiro, Agent Skills](https://kiro.dev/docs/skills/)（2026-11-18 更新）、[Kiro, Specs](https://kiro.dev/docs/specs/)（2026-11-12 更新）：Specs のページには置き場所の記載が無く、`.kiro/specs` は Permissions のページに出る。
- [Claude Code, Best practices](https://code.claude.com/docs/en/best-practices)（日付なし）：「Give Claude something it can run that comes back pass or fail.」「If something has to happen every time, make it a hook, a deny rule, or a managed setting.」
- [Codex, AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)、[kodustech/agent-readiness](https://github.com/kodustech/agent-readiness)（`linter`、`formatter`、`pre-commit-hooks`、`test-script`、`ci-runs-tests`、`lock-file`。`ai-context` は AGENTS.md を挙げていない）、[Factory](https://factory.com/news/agent-readiness)（2026-01-20。pre-commit が無いと「ten minutes for CI feedback instead of five seconds」）、[Böckeler](https://martinfowler.com/articles/harness-engineering.html)（2026-04-02。guides と sensors、keep quality left）、[Anthropic](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)（2025-11-26。進み具合のファイルと git の履歴、テストの道具）。
- BP-P2 の出典（Claude Code の Commands、Codex の Rules）は 2026-11-20 に読んだもので、今回は読み直していない。BP-P1・BP-P2 の確認日は 2026-11-20 にした。

### 変更

- `references/best-practices.md`（集の版1、15項目）と `scripts/practices.py`（項目ごとの判定）を足した。状態は「満たしている」「満たしていない」「当てはまらない」「未確認」。当てはまって満たしていない項目が候補（`practice:<ID>`）。兆しのある候補を先に並べる。
- 集約に「ベストプラクティス集との照らし合わせ」の節（`--section practices`）を足した。見出しに集の版と、いちばん古い確認日（何か月前か。表示だけ）を出す。
- 見送った項目（台帳で、対象の型が `practice:<ID>` の案を見送ったもの）は、集のその項目の確認日が見送った日より新しくなるまで、候補にしない。採用した項目は、追跡に今の状態を出す。
- 骨組みを版7にした。ハーネスの診断の後に照らし合わせの節を置き、骨組みを作った時点の行と候補を state の `report_practices` に控える。検査は控えから行を作り直す。候補と未確認の項目は、対象の型と一歩先の案の手がかりに書ける。質問の手がかりには、控えにあるすべての項目を書ける。集の文面の引用は不一致にする。
- 台帳の対象の型に `practice` を足した。常時読み込みの目安（8,000 文字）は `harness.py` に移し、集約と BP-G3 で同じ値を使う。

### 自動テストで確かめたこと

- 集の ID と判定の ID が一致し、重複が無い。各項目に、何のためか、当てはまる条件、満たした証拠、案の形、聞き方、出典（URL）、確認日がある。集を読めないときは、隠さずに出す。
- 項目ごとの判定（空のプロジェクト、テストと決まり、package.json と Makefile、終了時・保存時の hook、リモートの有無と CI、lint と pre-commit、deny 規則（すべてを止めて除外する形を含む。kiroku 自身の定義は数えない）、秘密のファイル、常時読み込みの長さ、型の候補と skill、ロックファイル（上の階層、固定した requirements）、spec と開始時の hook）。
- 兆しのある候補が先に並ぶ。見送った項目は候補から外れ、経過した日数では戻らず、その項目の確認日が新しくなったときだけ戻る。ほかの項目の更新では戻らない。同じファイルから同じ結果になる。
- レポート：版7の節が控えから書かれる。候補と未確認の項目は対象の型に書け、満たしている項目と当てはまらない項目は書けない。一歩先の案の手がかりにも書ける。骨組みの後に照らし合わせが変わっても、控えで照合して通る。控えの行を書き換えると不一致。控えが無いときは、今の照らし合わせで見て、そう表示する。集の文面の引用は不一致。採用した項目の追跡に、今の状態が出て、満たすと変わる。
- 版6（`e727611`）で作って検査を通ったレポートが、更新した後も通る。

### 気づいた誤り（コミットの前に直した）

最初の実装で、骨組みを作るときの控えの変数を、同じ関数の後ろにある記録フックのエラーの行数の控えが上書きしていた。state に違う値が控えられ、検査が「控えが無い」として今の照らし合わせを使っていた。足したテスト（控えの中身を確かめるもの）で見つけ、変数の名前を分けて直した。

### 今の版との比較（スクリプトで再現。LLM は動かしていない）

今の版（`e727611`）には、外の知見と照らし合わせる仕組みが無い。デモのコピーで、新しい版の照らし合わせを確かめた。

| ケース | 新しい版 |
|---|---|
| デモそのもの | 候補：BP-G2（常時読み込みの決まりのテストのコマンドが `make test` で、記録設定の `run_tests\.py` に当たらない）、BP-S2（保存時の hook `check-python-syntax` は、テストにも lint にも当たらない）、BP-S3、BP-S4。未確認：BP-P1（実行前の hook `guard-shell` があると添える）、BP-P2、BP-R1、BP-R2。当てはまらない：BP-S5、BP-P3、BP-G3、BP-G4、BP-E1（外部の依存が無い） |
| D31（リモートが無い） | BP-S5 は「当てはまらない」。リモートを足すと「満たしていない（CI の定義が無い）」になる |
| D32（BP-S4 の案を 2026-11-24 に見送った） | BP-S4 は「見送り中」で、候補から外れる。見送りを集の確認日（2026-11-24）より前の日付にすると、「見送ったが、その後に集のこの項目が更新された」として候補に戻る |

### 未検証

- 実機で、エージェントが候補を質問にし（集の聞き方と言い換え）、答えから `practice:<ID>` の案を作るか。未確認の項目を、会話から判断できるか。
- Kiro の規則の当たり方（`*`、`**`、全体に当てるか）は、資料の書き方からの近似。実機で確かめていない。
- 項目の当てはまる条件の近似（テストのファイルの形、コードの拡張子、秘密のファイルを3階層まで探す、など）が、ほかのプロジェクトで妥当か。最初の値で、根拠は薄い。

既存の導入先への影響は「`--update` だけで済む」。集と判定のファイルが増え（`--update` で配られる）、骨組みの版が7になり、state に `report_practices` が増える。以前の版のスクリプトはこの項目を読まない。設定、記録、ノート、台帳の形式は変えていない（台帳の対象の型に `practice:` が増えるだけ）。

## 2026-11-24：承認の繰り返しからの許可の提案（骨組みの版8。自動テストと比較のみ。実機は未検証）

[best-practices-design.md](best-practices-design.md) の段d（7節）を実装した。設計では「実機で承認の記録の形を確かめた後」としていたが、確かめ済みの値だけを数える形で先に実装した。確かめ済みなのは、CLI 3.3.0 で見た `selectedOption=accept`（承認画面で承認）、`selectedOption=reject`（Deny）、`outcome=cancelled`（Escape と承認画面の Ctrl+C）である（この文書の 2026-11 の記録）。Always allow を選んだときの値は確かめていないので、知らない値として「不明」に数え、承認には数えない。

### 変更

- `scripts/approvals.py`（新規）：開発のセッションのログで、`tool_call` と同じ `toolCallId` の `interaction_resolved` を結び、承認・拒否・取り消し・不明を数える。振り返りを始めた後の会話と、kiroku 自身のコマンドは数えない。
- 見出し：シェルは、作業場所の指定・リダイレクト・パイプ以降を除いた実コマンドの最初の2語。読み取りの道具とテスト・lint の道具は最初の1語。web の取得は取得先のドメイン、ほかはツールの名前。
- 危険の区分（読み取り、手元の検査、手元の書き換え、外に出る、任意のコードの実行、取り返しがつかない、区分を決められない）を、コマンドの文面から決める。つないだコマンドは、いちばん危ないもの。
- 許可の候補：別々の `approval_min_sessions`（既定2。最初の値）つ以上のセッションで承認し、一度も拒否が無く（拒否は期間の外も見る）、危ない3区分でないもの。取り返しがつかない操作の承認は、deny の案の候補に出す。
- 規則の案：観測したコマンドの違いを覆う、いちばん狭い段（完全一致、2語＋`*`、1語＋`*`）。`sudo`・`env` で始まるものは完全一致だけ、`*` だけの規則は出さない。危険な変形（`git push --force origin main`、`python3 -c …`、`python3 -m http.server`、`make deploy` など）に当たる段は使わない。`git diff *` は引数の無い `git diff` に当たらないので、引数の無い形を観測していれば、その完全一致も規則に入れる。
- 集約に「承認の記録」の節（`--section approvals`）。ワークスペースの規則のファイル（`~/.kiro/workspace-roots/<hash>/permissions.yaml`。`<hash>` はログの置き場のフォルダ名）を示し、最後の承認の後に変わっていれば「もう規則にしてあるかもしれない」と添える。
- 骨組みを版8にした。許可の候補を、検証値の対象の型（`approval:<見出し>`）に入れる。型ごとの負担（D6）の分母は、発話があるセッション。版7までのレポートは、承認の候補を含めない検証値で比べる。
- 採用した案の追跡：`approval:<見出し>` は、承認の時刻を発生として数える。追跡の境目の前の期間は、ログから読んだ承認の時刻も、記録の始まりに含める。
- ベストプラクティス集の BP-P2 を判定する（候補があれば「満たしていない」、読めるログが無ければ「未確認」）。
- 設定 `approval_min_sessions`（1 以上の整数）。不正な値は既定値で動き、インストーラーが知らせる。

### 自動テストで確かめたこと

- 承認の組み合わせと数え方、2セッションで候補、規則の案（`git diff`、`git diff *`）、検証値の対象の型、集約の表示。
- D34：1つのセッションで10回承認しても候補にしない。D35：`git push` は「外に出る」で、`git commit` は拒否したことがあるので候補にしない。
- 取り返しがつかない操作は deny の案の候補。知らない値（`selectedOption=accept_always`）は承認に数えず、別に出す。振り返りの後と kiroku 自身のコマンドは数えない。期間の外の承認は候補に数えないが、期間の外の拒否は外す理由になる。設定の数（3）と不正な値（0）。ワークスペースの規則のファイルの見つけ方と、更新時刻での注意。
- 規則の段（完全一致、2語、1語、`sudo`、そろわない先頭、危険な変形）。案の規則は、危険な変形のどれにも当たらない。区分（22 例）。見出し。
- レポート（版8）：承認の候補が対象の型になり、検査を通る。候補に無い見出しは不一致。採用すると、承認の回数で追跡される。版7（`cdf4c0b`）で作って検査を通ったレポートが、承認のログがあっても通る。

### 気づいた誤り（コミットの前に直した）

- 以前の版の形にそろえた集約（承認の記録が無い）で、質問の手がかりのために集約の文面を作るとき、承認の節が無いことを考えておらず、版7以前のレポートの検査が「レポートを検査できません」で止まっていた。足したテストで見つけ、節が無いときは出さないようにした。
- 採用した案の追跡で、境目の前の期間を「記録の始まり」で切っていたため、フックの記録より前のログにある承認が数えられていなかった。承認の時刻も、記録の始まりに含めた。

### 今の版との比較（スクリプトで再現。LLM は動かしていない）

デモのコピーに、実測した形のログ（セッションD1〜D3、demo-cases.md の「承認の場面」）を置き、集約を比べた。

| ケース | 今の版（`cdf4c0b`） | 新しい版 |
|---|---|---|
| D33（テストを2セッションで承認） | 承認を数えない。BP-P2 は「未確認」 | 許可の候補 `approval:python3 run_tests.py`（手元の検査）。規則の案は完全一致 `python3 run_tests.py`、当たらない例は `python3 -c 'import os'`、`python3 -m http.server`。BP-P2 は「満たしていない」 |
| D34（`git log` を1セッションで10回） | — | 候補にしない（承認したセッションが1件） |
| D35（`git push` を毎回承認、`git commit` は承認と拒否） | — | どちらも候補にしない（危険の区分、拒否したことがある） |

### 未検証

- 承認画面で Allow と Always allow を選んだときの値（Allow は `accept` と見ているが、実行を中断した場面だけ）。Always allow で保存される規則の場所と中身。
- Kiro の規則の当たり方（`git diff *` が `git diff` に当たるか、文字列全体に当てるか）。
- 既定の権限で、読み取りだけのコマンドに承認画面が出るか。出なければ、読み取りの候補は出ない。
- ワークスペースの規則のファイルの場所（`<hash>` の対応は CLI 3.2.1 で見たもの）。

既存の導入先への影響は「`--update` だけで済む」。判定のファイルが増え（`--update` で配られる）、骨組みの版が8になり、設定 `approval_min_sessions` の既定値はコードにある。以前のレポートは作ったときの版の規則で検査する。記録、ノート、台帳の形式は変えていない（台帳の対象の型に `approval:` が増えるだけ）。

## 2026-11-24〜25　Issue #24 の実機確認

Kiro CLI 3.5.0（V3）、Ubuntu 22.04.5 LTS（Linux 6.8.0-149-generic、x86_64）、Python 3.10.12、kiroku main `e2cacd7`。probe と demo に個別に導入し、両方で doctor の設定一致を確認した。

### 手順1　既定エージェントの承認

| コマンド | 承認画面・選択 | pending_interaction | outcome | selectedOption |
|---|---|---|---|---|
| `git status` | なし | なし | 解決イベントなし | 同左 |
| `python3 run_tests.py` | Allow once | あり | selected | accept |
| `git log --oneline -3` | なし。予定の Deny は選べず | なし | 解決イベントなし | 同左 |
| `ls -la` | Escape | あり | cancelled | キーなし |
| `git diff HEAD` | Create allow rule → 2語 → workspace | あり | selected | always-accept |

解決イベントに共通するほかのキーは `type`、`toolCallId`、`executionId`、`consentRound`、`modelToolUseId`、`_meta`。最後の行は `_meta.kiro.consentOutcome` に `scope=workspace`、`applied=true`。保存先は `~/.kiro/workspace-roots/5b0ddab4fe8cb247/permissions.yaml`。規則は `capability: shell`、`effect: allow`、`match: ["git diff *"]`。セッションログの親ハッシュと一致した。ユーザー共通の `~/.kiro/settings/permissions.yaml` は前後とも存在しなかった。

### 手順2　同じ probe の新しいセッション

| コマンド | 承認画面 | 手順書の画面予想との比較 |
|---|---|---|
| `git diff` | なし | 不一致。kiroku の近似では引数なしは別の完全一致 |
| `git diff --stat` | なし | 一致 |
| `git -C . diff` | あり。Deny once、selected / reject | 一致 |
| `git diff --stat && git status` | なし | 個別の2コマンドがどちらも通る観察と整合 |

画面の有無は実測した。保存前に引数なしの `git diff` を測っていないため、どの規則が許可したかは未検証。複合コマンドの内部判定もログからは特定できない。

### 手順3・4　開発の会話と初回の振り返り

`demo-cases.md` の「実機での会話」の発話を記載順に入力した。A（医療カテゴリ、報告順、日本語コミットと今回の比較月）は `sess_f3f8babc-858e-4b50-8498-b25bd2e0b2cc`、B（再テスト、報告順、整数円）は `sess_0b52a3e6-3fdb-4c4b-bf41-838bc5c1358b`、C（月の集計後に待つ、合計と返金の確認）は `sess_3b46a284-1cea-4f3d-9378-fd3a3f977146`。

初回は `aggregate.py --since last --require-agent` で開始。未読3件を読んでから「上から重要な順です。全部に答えなくて構いません」と表示し、5問でターンを終えた。件数の上限は「なし」。順に、C の合計・返金の確認、BP-P1 の危険操作に対する規則、BP-S2 のテスト hook、BP-S3 の lint、BP-S4 の pre-commit が手がかりだった。Q1 に通常分と返金の合格条件、Q5 に「使いたい」、Q2〜Q4 に「わからない」と回答した。

初回レポートは demo の `.kiro/kiroku/data/reports/2026-11-24-2355.md`。途中の検査では短縮したセッション ID を照合できなかった。エージェントが修正した完成版を試験者が検査して終了コード0。台帳は `2026-11-24-2355#P1 [見送り]`（`practice:BP-S4`）、P2〜P5 は未判断。

| ケース | 判定 | 根拠 |
|---|---|---|
| D28 | 合格 | D5 とレポートはいずれも CI の「match に一致する行が無い」。コメントをテスト実行の根拠にしていない。実コマンド側にもコメントを書く対照条件は今回の実機手順の対象外。 |
| D30 | 不一致 | C のノートと集約で月の合計と返金を手作業の確認として拾い、Q1 で合格条件を聞いた。回答後、固定例の既存テストで確認済みと扱い、実際の月次合計の照合を自動にする案（V3）は5案に含まれなかった。 |
| D31 | 合格 | リモートを追加する前の集約は BP-S5 を「当てはまらない」と表示。初回の5問・5案に CI の推奨はなかった。 |

### 手順5・6　承認と2回目の振り返り

| セッション | テスト | コミット | push / log |
|---|---|---|---|
| D1 `sess_5d86a8d3-3ee2-4a86-a669-87bbc583e097` | Kiro がコピー外の `/workspace/README.md` を参照しようとしたため中断。テストの承認は未実施 | Allow | 手元の bare に `git push origin main`、Allow |
| D2 `sess_a213c450-9ccf-462f-a2cd-113e8282fd49` | `python3 run_tests.py`、Allow | 作業ツリーがクリーンでコミット呼び出しなし。Deny を選ぶ画面なし | 同じ bare に同じ push、Allow |
| D3 `sess_a537dd16-4f21-4117-a55f-f9ff98def3f4` | — | — | `git log --oneline -1` を別々に10回実行。承認画面0回 |

D1 の push 前、試験者の操作補助が読み取り専用の `git remote -v` を含む事前確認も停止させた。同じセッションで push の依頼を再開した。集約に出たこの事前確認の取り消しには試験操作の影響がある。

2回目の直前の `--since last` の期間開始は `2026-11-24T14:55:04.170944+00:00`。初回レポートの `until` と一致し、初回の未読は0件。新しい未読は D1・D2・D3 の3件のみ。2回目も冒頭に「上から重要な順です。全部に答えなくて構いません」があり、リモート追加後の CI 定義のコメントを手がかりに BP-S5 を1問だけ聞き、ターンを終えた。見立てに「はい」と回答した。テスト、log、push、commit の許可について質問はなかった。

| ケース | 判定 | 根拠 |
|---|---|---|
| D29 | 前提不足 | 通常の引き継ぎで期間開始と未読一覧は期待どおり。3週間経過、7日より前の未読、前回未読が残る分岐は今回の実機では未検証。 |
| D33 | 不一致 | D1 のテスト依頼は中断したが、同じ demo の B と D2 で完全一致の `python3 run_tests.py` に各1回 Allow、拒否なし。集約は7ログを読み「許可の候補（0 件）」。対象の質問が出ず、許可を自動にするかの回答には進めなかった。 |
| D34 | 前提不足 | D3 は同じ1セッションの10呼び出しを確認。人による承認が0回で「10回承認」の入力を作れなかった。 |
| D35 | 不一致 | push はD1・D2で各1回 Allow。許可の質問はなかったが、集約の「候補にしない承認」は `read_file` の取り消し1回だけで、期待した push の「外に出る」行が欠けた。commit の拒否を含む分岐は前提不足。 |

CLI 3.5.0 の上記テストと push で、画面の選択を記録した `interaction_resolved.selectedOption=accept` と `pending_interaction` を照合した。対応するシェルの `tool_call.toolCallId` と承認の `toolCallId` が異なる。kiroku はこのキーの一致を必要とする。手順1の probe を別に集約しても「知らない答え」の表示はなかった。

2回目の質問への回答直後、Kiro のターンが `status=failed`、`stopReason=error` で終了した。診断ログの理由は `monthly usage limit`。2回目のレポートは生成されず、独立した `--check-report` は未実施。承認の案の本文（完全一致、当たる例と当たらない例、段・範囲、対象ファイル、エージェント定義、「未検証」の表記）は検査できなかった。

| ケース | 判定 | 根拠 |
|---|---|---|
| D32 | 未実施 | 台帳の見送り、2回目の集約の「見送り中」、BP-S4 を質問しなかったことまで確認。月間利用上限でレポートが生成されず、案にも出さないという条件は未検証。 |

### 手順7　旧版からの更新

`7ae4802` を新しい update コピーに導入した。旧版のテスト依頼も月間利用上限でツール呼び出し前に終了。旧版の開発セッションを同じ ID で再表示し、開いたまま `e2cacd7` の `--update` を実行した。終了コードは0。インストーラーの出力は次のとおり。

```text
新規  .kiro/skills/kiroku-retro/references/best-practices.md
新規  .kiro/skills/kiroku-retro/scripts/approvals.py
新規  .kiro/skills/kiroku-retro/scripts/practices.py
更新  .kiro/skills/kiroku-retro/SKILL.md
更新  .kiro/skills/kiroku-retro/references/harness-guide.md
更新  .kiro/skills/kiroku-retro/scripts/aggregate.py
更新  .kiro/skills/kiroku-retro/scripts/answers.py
更新  .kiro/skills/kiroku-retro/scripts/citations.py
更新  .kiro/skills/kiroku-retro/scripts/diagnose.py
更新  .kiro/skills/kiroku-retro/scripts/harness.py
更新  .kiro/skills/kiroku-retro/scripts/ledger.py
更新  .kiro/skills/kiroku-retro/scripts/notes.py
更新  .kiro/skills/kiroku-retro/scripts/record.py
更新  .kiro/skills/kiroku-retro/scripts/reportcheck.py
更新  .kiro/skills/kiroku-retro/scripts/skeleton.py
そのまま（導入先の設定）  .kiro/kiroku/config.json
新規 3、更新 12、変更なし 10
config.json に無い項目は、既定値で動きます（書き足す必要はありません）：
  sessions_dir = null
  write_tools = ["fs_write", "fs_append", "str_replace"]
  ignore_paths = [".kiro/kiroku/data/**", ".git/**"]
  output_tail_chars = 2000
  max_dirty_files = 500
  retro_read_chars = 50000
  retro_output_chars = 20000
  test_start_max_seconds = 3600
  one_step_max = null（上限なし）
  question_max = null（上限なし）
  approval_min_sessions = 2
完了。テスト結果の記録は .kiro/kiroku/config.json の tests を設定してください。
```

更新前後で `events.jsonl` は1行から2行になり、いずれも `type=prompt`。更新後に同じセッションで依頼が記録されることを確認した。両時点で `record-errors.log` は存在しなかった。再度のテスト依頼も月間利用上限で終了し、テスト実行後のイベントの互換性は未検証。版5のレポート生成と更新前後の検査、および更新先での版8レポート生成・検査は未実施。検査の終了コードは得られなかった。

既存の導入先への影響は、今回の検証記録の追記については「なし」。

## 2026-11-25：実機の確認（CLI 3.5.0）で見つかった不一致への対応（自動テストのみ）

上の記録（2026-11-24〜25）の不一致と、確かめられた値に合わせて直した。

### 直したもの

- **承認の答えと呼び出しの結び方（D33・D35 の不一致の原因）**：kiroku は、答え（`interaction_resolved`）と呼び出し（`tool_call`）の `toolCallId` が同じことを前提にしていた（CLI 3.3.0 の形）。CLI 3.5.0 のシェルの呼び出しでは違っていた。そのうえ、結べなかった答えを黙って捨てていた（AGENTS.md の「読めないときは黙って捨てず、記録の不足として表に出す」に反していた）。`excerpt.pair_interactions` を足し、同じ `toolCallId`、答えの `modelToolUseId` を値に持つ呼び出し、ログの順（答えの後の、まだ結んでいない最初の呼び出し。`executionId` があれば同じもの）の順に結ぶ。結べない答えは数えず、件数を集約に出す。集約には、結び方ごとの件数も出す。
- **止められた呼び出しの集計**：同じ前提で、CLI 3.5.0 では、利用者が Deny した呼び出しを「実行前に止められた呼び出し」に数えていた（このシリーズの前からある不具合）。同じ結び方を使うようにした。
- **Always allow**：`selectedOption=always-accept`（範囲は `_meta.kiro.consentOutcome.scope`）を、知らない値ではなく「Always allow」として数える。その見出しは、もう規則があるので許可の候補にしない。追跡では、承認の1回として数える。
- **D30（手作業の確認の案）**：エージェントが、固定の例を確かめる既存のテストを、利用者の目視の代わりとみなした。手引きと SKILL.md に、案が確かめる対象は利用者が実際に見ているもの（実データ、実際の出力）で、固定の例のテストでは確認済みとみなさない、と書いた。
- **手順書と場面**：次の実機の確認で前提不足にならないよう、手順1に引数なしの `git diff` の対照（規則の保存前）と、`toolCallId` の対応の記録を足した。D34 は承認画面が出る `ls -la` に、D35 のコミットは変更を作ってから行うように、D1 は「このフォルダの README.md」と明示するようにした。

### 自動テストで確かめたこと

- `toolCallId` が違う答え（`modelToolUseId` は答えの側だけ）を、ログの順で結び、D33 の候補が出て、D35 の push が「外に出る」で候補にならない。この2つのテストは、直す前のコードで落ちることを確かめた。
- 呼び出しの無い答えは、数えずに件数を出す。Always allow は候補にせず、範囲を出し、知らない値にしない。Deny した呼び出しを、止められた呼び出しに数えない（直す前のコードで落ちる）。

### 確かめていないこと

- CLI 3.5.0 で、呼び出しのどのキーに答えの `modelToolUseId` の値があるか（実ログで見ていないので、kiroku は呼び出しの値をすべて探し、見つからなければログの順に結ぶ）。ログの順の結び方が、並行した呼び出しで正しいか。
- D29（3週間の空き）、D32 の案の側、D34、D35 の拒否、更新の後のテスト実行の記録、版5と版8のレポートの検査（上の記録で前提不足・未実施のもの）。

### 気づいたこと（動きは変えていない）

- 版8のレポートは、承認の答えを結べるようになったので、更新した後に検査し直すと、検証値の対象の型（`approval:…`）が増えることがある。版8は公開から間もなく、実機の確認でも作られていないため、版は上げていない。

既存の導入先への影響は「`--update` だけで済む」。
