# 段階1：検証結果

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
