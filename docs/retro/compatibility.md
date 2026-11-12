# CLI V3・IDE・PowerShell 対応の調査

調査日：2026-11-12。公式ドキュメントで確認した仕様と、実機確認が必要な点を分ける。
この文書は互換性の設計根拠であり、Windows／IDE の実機試験に合格したという記録ではない。

## 対象と公式に確認できたこと

| 項目 | CLI V3 | IDE 1.x |
|---|---|---|
| ハーネス | CLI 3.0 以降は V3 が既定 | 同じ統合ハーネス |
| フック | `.kiro/hooks/*.json`、`version: v1`、`trigger`／`action` | 同じ形式 |
| 記録に必要なトリガー | `UserPromptSubmit`、`PostToolUse`、`PostFileSave` | いずれも対応 |
| コマンドフック入力 | プロジェクトルートで実行、標準入力に JSON | 同じ契約 |
| 専用エージェント | `.kiro/agents/` の JSON または Markdown | 同じ構成形式 |
| 会話の持ち出し | `/chat save backup.zip` | チャットタブの Export Chat |

根拠：[CLI 概要](https://kiro.dev/docs/cli/)、[Hooks](https://kiro.dev/docs/hooks/)、[トリガー一覧](https://kiro.dev/docs/hooks/types/)、[エージェント設定](https://kiro.dev/docs/custom-agents/configuration-reference/)、[CLI セッション管理](https://kiro.dev/docs/cli/chat/session-management/)、[IDE 1.0](https://kiro.dev/docs/ide/whats-new-v1/)。

現行 Hooks ページを正とする。古い IDE 0.x の `.kiro.hook` や `when`／`then` の例を混ぜない。IDE 0.x は今回の対象外。

Windows の公式要件は、CLI が Windows 11（PowerShell）、IDE が Windows 10/11 64bit。kiroku の Python と Git は別途必要になる。[インストール要件](https://kiro.dev/docs/cli/installation/)

## ツール名と専用エージェント

現行エージェント設定の公式フック例は、シェルの内部 ID として `execute_bash` と `execute_pwsh`、書き込みには `fs_write`、`fs_append`、`str_replace`、`delete_file` を挙げる。`execute_powershell` や旧 IDE の `executePwsh` を推測で採用しない。設定の `tools: ["shell"]` はカテゴリ指定であり、入力 JSON の `tool_name` と同じ名前とは限らない。[エージェント設定：Hooks field](https://kiro.dev/docs/custom-agents/configuration-reference/#hooks-field)

同じ公式リファレンスは IDE 1.0／CLI V3 共通で、`permissions.rules` の `allow`／`ask`／`deny`／`exclude` と `resources` の `skill://` パス・glob を定義している。したがって既存の JSON 専用エージェントを IDE 用に別形式へ複製する必要はない。ただし PowerShell で呼ぶ Python コマンドとシェル許可規則は一致させる必要がある。[エージェント設定：Permissions／Resources](https://kiro.dev/docs/custom-agents/configuration-reference/)

`execute_pwsh` の実際の `tool_input` と終了コードを含む `tool_response` の完全な契約は、今回確認した公式資料だけでは確定できない。Linux CLI の `Exit Code:` 文字列形式を全環境で実測済みとは扱わない。

## 会話ログの取得方針

IDE の公式 Export Chat は、セッション ID のディレクトリ内に `session.json` と会話全体の `messages.jsonl`、サブエージェントがあれば `sub-executions/*.jsonl` を格納する ZIP を出力する。[IDE のエクスポート](https://kiro.dev/docs/ide/whats-new-v1/#export-sessions)

一方、CLI セッション管理ページには SQLite と V3 ZIP の記述が混在し、`messages.jsonl` の行ごとのスキーマを安定した公開 API として定義していない。リポジトリの [実測結果](verification.md) にある `payload.type` 形式を、将来も保証される仕様と読み替えない。[CLI セッション管理](https://kiro.dev/docs/cli/chat/session-management/)

設計上の提案：

- 会話の表示では、ユーザー発話と AI 応答を同じセッションログから読む。時系列・欠落・切り詰めの扱いをそろえる。
- フックは実行時の Git 状態、内容ハッシュ、JUnit の取り込みを継続する。これらは会話ログだけでは正確に復元できない。
- 発話フックは本文を保存せず、時刻とセッション ID を書き直し区切りとして残す。ログ欠落時は会話に基づく判断を保留する。
- ログのルートを設定で指定できるようにする。IDE の ZIP を展開した場所も指定できれば、内部保存場所の変更に対応しやすい。
- 対象プロジェクトのイベントに登場する session ID だけを対応づける。全プロジェクトの会話を無条件に集約しない。

これは公式仕様の引用ではなく、現在の実装と公開仕様からの設計判断。

## 既存ハーネスとの比較対象

MCP は `.kiro/settings/mcp.json` に加え、エージェント内の `mcpServers` と `includeMcpJson` も確認する。ユーザーレベルの設定とプロジェクト設定は統合され、プロジェクト側が優先するため、プロジェクト内にファイルがないことだけで「MCP 未設定」と断定しない。[MCP 設定](https://kiro.dev/docs/mcp/configuration/)

改善提案には、観測された問題、既存ファイルと該当規則、既存規則が効かない理由、変更または新規作成の具体案、期待する効果、検証方法を対応づける。設定がないこと自体を欠陥とせず、利用上の問題を根拠にする。現在のファイルと過去の事象を比較するときは、当時の設定と同じとは限らない点も区別する。

## 実機で残すべき証拠

CLI Linux、CLI Windows PowerShell、IDE Windows の各環境で、バージョン、OS、Python 起動コマンドとともに以下を確認する。

1. 3 本のフックの読み込みと発火、入力のキー、session ID。
2. シェル成功・失敗・取り消し・権限拒否の応答形式。
3. 日本語発話と空白を含むパスの文字化けの有無。
4. ファイル作成・置換・追記後のハッシュと、JUnit の再読防止。
5. 会話ログからユーザー／AI 両方を読めること、欠落時の判断保留。
6. 専用エージェントのスキル起動、レポート保存、対象外への書き込み拒否。

Python 単体テストの成功と、これらの製品連携試験の成功は別に報告する。
