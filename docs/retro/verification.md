# 段階1：検証結果

2026-11-14のデモ調査後の修正・検証と、CLI 3.3.0での実機確認は末尾に追記しています。以下の段階1の結論は2026-11-12時点の記録です。

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
