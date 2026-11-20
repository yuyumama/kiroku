# ハーネスの地図と、承認の繰り返しからの権限の提案：調査と設計案

2026-11-20。調査と設計案（未実装）。決めてほしいことは8節にある。

## 要点

- 今の kiroku の案は、失敗・指摘・繰り返しの説明といった「痛み」から出る。同じ作業を続けると同じ型しか出ず、直り切ると案が止まる。一歩先の案（V1〜V8）も、手がかりはリポジトリの中（ファイルとログ）だけである。利用者が知らない仕組みは、痛みとして表に出ない限り提案されない。
- そこで、外の基準として「ハーネスの地図」を kiroku に同梱する。地図は、Kiro・Claude Code・OpenAI の公式資料と、到達度を測る道具（Factory、Kodus、agentready）から作る。項目ごとに、出典、見つけ方、前提を持たせる。スクリプトが地図と今の状態を比べ、「次の一歩」の候補を診断値（`D7`）として出す。点数は付けない。
- 点検は、人が起動しない。週次の振り返りの中で、スクリプトが「点検の回」かを決める（前回の点検から28日たった、または前回のレポートに案が無かった）。点検の回は、地図の次の一歩を1つ、質問機能で利用者に確かめてから案にする。
- 毎回人が承認している操作は、許可の規則の案にする。数えるのは、ログに残る承認の記録（`interaction_resolved`）である。似た道具は「ツールの呼び出しの回数」を数えるが、kiroku は「人が実際に承認した回数」を数えられる。
- 承認の案は、安全と天秤にかける。Kiro の段（完全一致、2語＋`*`、1語＋`*`）のうち、観測したコマンドを覆う最も狭いものを選ぶ。危険の区分で出し方を変える。外に出る操作は許可を出さず、取り返しのつかない操作と任意のコード実行は、許可ではなく deny を勧める。規則が観測したコマンドに当たり、危険な変形に当たらないことは、スクリプトが確かめる。
- 案の反映は、ファイルの編集を求めない。次にその承認画面が出たら「Always allow」を選び、指定の段と「This workspace」を選ぶ、という手順で示す（YAML の文面も添える）。
- OpenAI の harness engineering の記事は、今回も読めていない（403）。

## 1. 解きたいこと

利用者の指摘（2026-11-20）：

1. 質問機能はよく動いている。ハーネスはドメイン知識を入れるもので、利用者にしか分からないことが多い。ログから見えることを対話で提案する方向は伸ばす。
2. ただし利用者は同じことを繰り返すので、同じようなログしか溜まらず、一歩先の改善が出ず、ハーネスのレベルが上がらないのではないか。外の見解（Claude や OpenAI のハーネスのベストプラクティスなど）を調べ、今の状態と kiroku のログと比べて、何がよいかを探せないか。
3. 定期の点検はよいが、月1回を人が起動するのは避けたい。人がすることは少なくしたい。
4. 毎回人が承認していることを、権限（permissions）に書く提案がほしい。安全性との天秤を取って。

## 2. 今の作りとの関係（コードで確かめたこと）

- 一歩先の案の手がかりは、集約の型と診断値 `D1`〜`D6` である（`diagnose.py`）。外の基準を表すものは、`D5`（テストの記録設定、kiroku 以外の hook、pre-commit、CI、lint の有無）だけである。
- 承認画面の結果は、すでにログから読んでいる。`prompts.unrecorded_calls()` が、`interaction_resolved` の `outcome` と `selectedOption` を `toolCallId` で呼び出しと結び、取り消しと拒否を見分けている。実機（Kiro CLI 3.3.0）で確かめた形は、拒否が `selectedOption=reject`、Escape が `outcome=cancelled`、承認した後に中断したものが `selectedOption=accept` である（[verification.md](verification.md) の 2026-11-15）。承認画面で「Always allow」を選んだときの値は、確かめていない。
- 振り返りは、週次に手で起動する前提である（README「週次を想定して手動で専用エージェントを呼びます」）。点検をこの振り返りに組み込めば、人の手は増えない。
- 採用した案の追跡（`ledger.tracking`）は、「対象の型」の発生回数を、反映の前後で比べる。承認の回数を型にすれば、規則を足した後に承認が減ったかを、同じ仕組みで数えられる。

## 3. 調べたこと

### Kiro の公式資料

| 資料 | 分かったこと | 設計への影響 |
|---|---|---|
| [Permissions](https://kiro.dev/docs/permissions/)（2026-11-18 更新） | 規則は capability（`shell`、`fs_read`、`fs_write`、`web_fetch`、`mcp` など）、`match`（glob）、`effect`（deny、ask、allow、evaluate）。シェルの glob は「`*` は任意の文字の並び」で、`**`・`?`・文字の組は使えない。`;`、`&&`、`\|\|`、`\|` でつないだコマンドは分けて1つずつ判定する（「`npm test *` の規則が `npm test ; curl attacker.com` に当たらないようにするため」）。優先は deny > ask > allow > evaluate で、範囲の間に優先は無く、いちばん厳しいものが勝つ | 案は1つのコマンドごとに作る。規則の当たり方を、この意味でスクリプトが確かめる |
| 同上 | 規則の置き場所は、ユーザー共通 `~/.kiro/settings/permissions.yaml`、ワークスペース `~/.kiro/workspace-roots/<hash>/permissions.yaml`（「リポジトリの外に、利用者ごとに置く。複製したリポジトリは規則を差し込めない」）、エージェント定義の `permissions`、そのセッションだけ、組織の管理設定 | 案には、どこに書くか（ワークスペースか、リポジトリの中のエージェント定義か）と、その違いを書く |
| 同上 | 承認画面の選択肢は、Allow（1回）、Always allow、Deny（1回）、Always deny。Always allow は、段（完全一致、2語＋`*`、1語＋`*`）と範囲（All workspaces、This workspace、This session）を選んで、規則として保存する。段は CLI 2.36 から。`sudo` や `env` で始まるコマンドには、2語や1語の段を出さない。「1つの操作を繰り返すなら2語の段、コマンド全体を信頼するなら1語の段」を勧める | 案は「次に承認画面が出たら、Always allow で、この段と This workspace を選ぶ」と書く。ファイルを編集しなくて済む |
| 同上、[Approve for me の紹介](https://kiro.dev/blog/introducing-approve-for-me/)、[CLI 2.36](https://kiro.dev/changelog/cli/2-36/) | Approve for me は、規則で決まらない操作を LLM の判定で通す・止める・聞くモード（`evaluate`）。ワークスペースの読み書きと、読み取りだけのシェル（システムの情報、git・cargo・npm・docker・kubectl・rustup の照会）は、そのまま許可する。既定では切ってある。有料の利用者向けで、地域の制限があり、判定の費用がかかる。「セキュリティの仕組みの代わりにはならない」。deny の例として、`rm -rf *`、`sudo *`、`.env` や `*.pem` の読み取りを挙げる | 区分を決められない承認が多いときの、もう1つの選択肢として示す。決まった規則（狭い allow と deny）を先に勧める |
| [Hooks のトリガー](https://kiro.dev/docs/hooks/types/)（2026-11-18 更新） | PermissionRequest（CLI 3.4.0 から）は、承認画面が出る直前に動く。規則で決まった呼び出しでは動かない。用途の例に「人がいない実行で、決まった承認をスクリプトで返す」「セッションが出したはずの承認画面をすべて記録する」がある。Agent Stop の用途に「コンパイルやテストを動かし、失敗をエージェントに返す」、File Save に「lint と整形」「変えたファイルのテスト」。ファイルのトリガーは、エージェントの変更にだけ反応する | 承認の記録は、まずログから読む（フックを足すと、古い Kiro での動きが未確認になるため）。地図の「検査」の項目の根拠 |
| [Steering](https://kiro.dev/docs/steering/)（2026-10-21 更新） | 基礎のファイル（product.md、tech.md、structure.md）。読み込みの条件（always、fileMatch、manual、auto）。よい書き方（1ファイルに1領域、理由と例を書く、秘密を書かない、定期的に見直す）。AGENTS.md は常に読み込まれる | 地図の「案内」の項目の根拠 |
| [Memory](https://kiro.dev/docs/memory/)（2026-11-18 更新） | memories は、会話をまたいで、コマンドや決まり、訂正、試して外した方法を覚える。利用者ごとにローカルに保存し、必要なときに取り出す。「一貫して守らせたい決まりは Steering に書く」 | 地図の項目にする。ただし、保存場所が書かれておらず、ログでの形も確かめていないので、最初の版では見つけ方を「未確認」とする |
| [CLI V3](https://kiro.dev/docs/cli/v3/)、[CLI 3.4](https://kiro.dev/changelog/cli/3-4/) | Spec（`/spec new`）、権限、hook、ワークフロー、tangent、introspect、`/memories` など | 地図の「手順と状態」の項目の根拠 |

### Claude Code の公式資料

| 資料 | 分かったこと | 採り入れる |
|---|---|---|
| [Best practices](https://code.claude.com/docs/en/best-practices) | 指示には、モデルが読むもの（CLAUDE.md、skill）と、ハーネスが必ず当てるもの（hook、deny 規則）がある。「リポジトリを読んでも分からないことだけを書く」。「通ったか落ちたかを返す、実行できるものを渡す」。守られない CLAUDE.md の行は hook に移す。強制するものは少なく保つ。deny 規則は、どの権限モードでも効く | 地図の「案内」「検査」の項目。決まりは少なく、守らせたいものは仕組みに、という原則 |
| [Permissions](https://code.claude.com/docs/en/permissions) | `*` は空白を含む任意の文字に当たる。つないだコマンドは、すべての部分が許可されていなければならない。読み取りだけのコマンド（ls、cat、grep、find、読み取りの git など）は、既定で聞かない。「コマンドの引数を絞ろうとする規則は壊れやすい」。deny は守りの境界ではない（`git push *` を禁じても `git -C . push` は当たらない）。ネットワークは curl・wget を禁じ、取得先を絞る | 引数まで絞る規則を案にしない。deny の案には、当たらない書き方が残ることを添える |
| [Commands](https://code.claude.com/docs/en/commands) | `/fewer-permission-prompts`：「会話の記録から、よく使う読み取りだけの Bash と MCP の呼び出しを探し、優先度つきの許可の一覧を `.claude/settings.json` に足す」 | 前例。ただし Claude Code は設定に書く。kiroku は案だけを出し、反映は人が行う |
| [Permission modes](https://code.claude.com/docs/en/permission-modes) | auto モードは、別のモデルが操作を判定する。「承認の画面は減るが、安全は保証しない」。deny 規則は判定の前に効く | Approve for me を示すときの注意 |

### OpenAI の公式資料

| 資料 | 分かったこと | 採り入れる |
|---|---|---|
| [Codex の rules](https://learn.chatgpt.com/docs/agent-configuration/rules)（旧 developers.openai.com/codex/rules） | `prefix_rule(pattern=[...], decision="allow"/"prompt"/"forbidden", match=[...], not_match=[...])`。複数が当たれば、いちばん厳しいものを取る。承認を求められたときに、Codex が規則の案を出すことがあり、「出された先頭部分をよく見てから受け入れる」。`codex execpolicy check` で規則を試せる。実験的な機能 | 案に「当たるべき例」と「当たってはいけない例」を付け、スクリプトが確かめる（`match`／`not_match` と同じ考え） |
| [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md) | ルートから今のフォルダまでの AGENTS.md を順に読み、近いものが後に来て優先される。32 KiB で読み込みを止める。例は短い箇条書き | 地図の「案内」の項目 |
| [承認とセキュリティ](https://learn.chatgpt.com/codex/agent-approvals-security) | 承認の方式（on-request、never）と、サンドボックス（read-only、workspace-write、danger-full-access）。バージョン管理されたフォルダの既定は workspace-write と on-request。ネットワークは既定で切る（後半は要約で読んだ） | ネットワークを使う操作は、許可を出さない区分にする |
| OpenAI「Harness engineering: leveraging Codex in an agent-first world」 | **未読**。openai.com はどの URL も 403。アーカイブの写しから読む方法は、アクセスの制限を迂回する操作として止められたので、行っていない。検索結果の要約だけを見た（ハーネスは、エージェントを制約し、知らせ、確かめ、直す仕組み、など） | 根拠にしない。利用者が本文を共有できれば、読んで地図に足す |

### 到達度を測る道具

| 道具 | 分かったこと | 採り入れる | 採り入れない |
|---|---|---|---|
| [Factory, Agent Readiness](https://factory.com/news/agent-readiness)（2026-01-20） | 8つの柱（Style & Validation、Build System、Testing、Documentation、Dev Environment、Code Quality、Observability、Security & Governance）と5つの段。段に上がるには、その段までの基準の80%を満たす。「段3を目指す」。基準は合否の二値で、多くはファイルの有無や設定の確認。LLM の判定を使う基準には、ばらつきがある | 合否の二値の基準。段（前提）で並べる考え | 点数と段の付与。LLM による判定 |
| [kodustech/agent-readiness](https://github.com/kodustech/agent-readiness)（約140 stars） | 7つの柱、5つの段、39の基準と、それぞれの見つけ方（ファイル、設定、依存の一覧、CI の定義の中のコマンド）。例：lint、整形、型検査、pre-commit、テストの枠組み、テストのコマンド、CI がテストを動かす、AI 向けの文書（CLAUDE.md など）、ロックファイル、`.env.example`、準備のスクリプト、版の固定 | 基準の候補と、見つけ方 | リポジトリの管理の基準のうち、エージェントの作業に関わらないもの（LICENSE、SECURITY.md、CODEOWNERS など） |
| [ambient-code/agentready](https://github.com/ambient-code/agentready)（約160 stars） | 13の分類と4つの段（重み付けの点数と、Platinum〜Bronze の認定）。段1は「テストの実行、型、CLAUDE.md、CI、README、ロックファイル」など | 段1の基準の考え | 重み付けの点数と認定 |

### 承認から許可を作る道具

| 道具 | 分かったこと | 採り入れる | 採り入れない |
|---|---|---|---|
| Claude Code `/fewer-permission-prompts`（公式。挙動は [classmethod の記事](https://dev.classmethod.jp/en/articles/claude-code-fewer-permission-prompts/)） | 最近の50セッションまでの会話の記録から、Bash と MCP の呼び出しを数え、3回以上のものを上位20件まで。既定で許可されている読み取りと、任意のコードを実行できるもの（`python3`、`docker exec` など）は外す。プロジェクトの設定に書く。ほかのプロジェクトのコマンドも候補に出てしまう | 回数の目安（3回）。任意のコードの実行を外すこと | 設定に書き込むこと。プロジェクトをまたいで数えること（kiroku はこのリポジトリのセッションだけを数える） |
| [Transcript-Driven Permission Allowlist](https://agentpatterns.ai/security/transcript-driven-permission-allowlist/)（agentpatterns.ai） | 「出すのは案で、書き込みではない」。観測した呼び出しを覆う、最も狭い範囲を案にする。引数まで絞る規則は避け、細かい判定は実行前のフックに任せる | 最も狭い範囲、案だけ出す | — |
| [oliver-kriska/claude-elixir-phoenix](https://github.com/oliver-kriska/claude-elixir-phoenix) の `phx:permissions`（約580 stars） | 最近14日のセッションから、コマンドを GREEN（読み取り、テストなど）・YELLOW（git commit、push、依存の導入など）・RED（rm -rf、sudo、kill、curl \| sh、git push --force、git reset --hard、chmod 777 など）に分ける。「RED は決して自動で許可しない」。書く前に差分を見せ、確認を取る | 危険の区分と、取り返しのつかない操作を許可しない決まり | 1語＋`*` に広げる書き方（観測より広くなる） |
| Codex のスマート承認（上の rules） | 承認を求められたときに、先頭部分の規則の案を出す | 承認の場で規則にする流れ（Kiro の Always allow と同じ） | — |

似た道具は、どれも「ツールの呼び出しの回数」を数えている。kiroku は、ログの承認の記録から「人が実際に承認した回数」を数えられる。毎回承認していることを、そのまま拾える。

見つからなかったもの：会話の記録から、外の基準（地図）と比べて「次の一歩」を提案する道具。到達度の道具はリポジトリのファイルだけを見て、会話の記録は見ない。

## 4. 設計案：ハーネスの地図

### 4.1 形

- `references/harness-map.md` を kiroku に同梱する。項目ごとに、ID、何か、なぜ（出典と確認した日）、見つけ方、前提、案の形、利用者に聞くときの質問の雛形を書く。
- 見つけ方はスクリプト（`diagnose.py`）が持つ。地図の ID とスクリプトの ID が一致していることは、テストで確かめる。
- スクリプトは、項目ごとに「ある／ない／未確認」を決める。前提を満たしていて「ない」項目を、次の一歩の候補 `D7:<ID>` として出す。点数や段の付与はしない。
- 地図はファイルなので、案の根拠は「引用（ファイル）：`.kiro/skills/kiroku-retro/references/harness-map.md`「…」」として、今の検査がそのまま確かめる。

### 4.2 最初の版の項目（案）

エージェントの作業に関わるものだけにする。並びは、前提の順（上ほど基礎）である。

| ID | 項目 | なぜ（出典） | 見つけ方 | 前提 |
|---|---|---|---|---|
| G1 | 毎回読ませる決まりがある（AGENTS.md か、常時読み込みの steering） | Kiro Steering の基礎のファイル、Codex の AGENTS.md、Claude の「リポジトリを読んでも分からないことを書く」 | ファイル | なし |
| S1 | テストの実行方法が決まっていて、kiroku が結果を取り込める | Claude の「通ったか落ちたかを返すものを渡す」、Kodus のテストの基準 | テストの記録設定 | なし |
| G2 | 決まりに、テストの実行方法が書いてある | Claude、Codex の AGENTS.md の例 | 常時読み込みの決まりに、テストの記録設定に当たる行がある | G1、S1 |
| S2 | 作業の終わりか保存時に、テストや検査を動かす hook がある | Kiro Hooks（Agent Stop、File Save の用途）、Claude の Stop hook、Fowler の keep quality left | kiroku 以外の hook の、トリガーと呼ぶコマンド | S1 |
| S3 | lint・整形の設定がある | Kodus、Factory | 設定ファイル（今の `D5`） | なし |
| P1 | 取り返しのつかない操作の deny 規則がある | Kiro Permissions の例、Claude の「deny はどのモードでも効く」 | リポジトリの中のエージェント定義の `permissions`。ワークスペースの規則はリポジトリの外なので、読めなければ未確認 | なし |
| P2 | 毎回承認している操作が、規則になっている | Kiro の承認の段、Claude の `/fewer-permission-prompts`、Codex のスマート承認 | ログの承認の記録（5節） | なし |
| P3 | 秘密のファイルの読み取りを止めている | Kiro Permissions の例（`.env`、`*.pem`） | 同上。リポジトリに `.env` などがあるときだけ候補にする | リポジトリに秘密らしいファイルがある |
| G3 | 決まりを、条件付きの読み込みに分けている | Kiro の読み込みの条件、Claude の範囲つきの決まり | 常時読み込みの合計が目安を超え、条件付きの steering が無い | G1 |
| G4 | 繰り返す手順が skill になっている | Kiro Skills、Claude skills | kiroku 以外の skill があるか | 繰り返しの型（観測）がある |
| S4 | コミットの前の検査（pre-commit）がある | Kodus、Factory、Factory の「pre-commit なら5秒、CI なら10分」 | 今の `D5` | S3 か S1 |
| S5 | CI がテストを動かす | Kodus | 今の `D5` | S1 |
| R1 | 大きな変更を、spec や計画として残している | Kiro Specs と Plan モード、Claude の「仕様を書いてから作る」、Anthropic の進み具合のファイル | `.kiro/specs/` | なし |
| R2 | セッションの始めに、状態や前提を渡す | Kiro Hooks（Session Start の用途）、Anthropic の進み具合のファイル | SessionStart の hook | 繰り返しの説明のうち、状態にあたるもの（V8）がある |
| R3 | 一貫して守らせたいことを、memories ではなく steering に書いている | Kiro Memory | 未確認（保存場所とログの形を確かめていない） | — |
| E1 | 依存を固定している（ロックファイル） | Kodus の段1 | ファイル | なし |

### 4.3 地図の更新

- 地図は、kiroku のリポジトリで更新し、`--update` で配る。振り返りのエージェントに web を調べさせない。同じ入力で同じ結果になり、出典を人が確かめられ、利用者のコードや会話を外に出さず、エージェントの権限を広げずに済むため。
- 出典と確認した日は、地図の各項目と `docs/retro/verification.md` に残す。
- 更新を人の手で思い出さなくて済むよう、月1回、このリポジトリで調査して地図の更新案を PR にする定期実行（クラウドのセッション）を置く案がある。人がするのは、PR を読んでマージすることだけになる。置くかどうかは、決めてもらう（8節）。

## 5. 設計案：点検を週次の振り返りに組み込む

- 集約が、「今回は点検の回か」を決めて、冒頭に出す。点検の回は次のどれかのとき（どちらも最初の値で、設定で変えられる）。
  - 前回の点検から `audit_days`（28日）以上たった。点検したことが無い。
  - 前回の書き終えたレポートに、型も案も無かった（痛みからの案が止まった）。
- 点検の回は、振り返りの手順に「地図を見る」を足す。`D7` の候補から、前提の順にいちばん上の1つを選ぶ。見送った案と、前回までの回答で「使わないと決めている」とされた項目は飛ばす。
- 選んだ項目は、利用者が知らない可能性が高いので、質問機能で確かめてから案にする（「知っていますか、使ってみますか」。見立てつき）。案は、一歩先の改善案の形で書き、手がかりに `D7:<ID>`、根拠に地図の引用を付ける。
- 骨組みを作るときに点検の回だったことを state に控え、次の点検の時期は、そこから数える。
- 人が起動するものは増えない（週次の `/kiroku-retro` のまま）。

## 6. 設計案：承認の繰り返しから、許可の規則を提案する

### 6.1 数えるもの

- 開発のセッションのログ（振り返りの会話は除く）で、承認画面で人が承認して実行した呼び出し。`tool_call` と、同じ `toolCallId` の `interaction_resolved`（`selectedOption` が承認を表すもの）を結ぶ。
- まとめ方：シェルはコマンド（先頭の作業場所の指定などを除いた、今の集約と同じ見出し）、MCP はサーバーとツール、web の取得は取得先のドメイン。
- 案にする目安は、3回以上、2つ以上のセッション（最初の値。設定 `approval_min` で変える）。Claude Code の `/fewer-permission-prompts` も3回を目安にしている。
- kiroku 自身のコマンドと、振り返りのセッションは数えない。

### 6.2 危険の区分と、出し方

| 区分 | 例 | 出し方 |
|---|---|---|
| 読み取り | `git status`、`git diff`、`git log`、`ls`、`cat`、`grep` | allow の案（2語＋`*` か完全一致） |
| 手元の検査 | テストの記録設定に当たるコマンド、lint、型検査、ビルド | allow の案（完全一致か2語＋`*`） |
| 手元の書き換え | `git add`、`git commit`、`mkdir`、`mv`、`cp` | 理由を添えて、完全一致の allow か、このまま聞く。質問機能で確かめる |
| 外に出る | `git push`、`curl`、`wget`、`gh`、`npm install`、`pip install`、`npm publish`、`docker push`、`ssh` | allow を出さない。このまま聞くことを勧める |
| 任意のコードの実行 | `python -c`、`bash -c`、`sh -c`、`node -e`、`eval`、`npx`、`docker exec` | 1語＋`*` は出さない。このまま聞くことを勧める |
| 取り返しがつかない | `rm -rf`、`git reset --hard`、`git push --force`、`git clean -f`、`sudo`、`chmod -R 777`、`kill -9` | allow を出さず、deny の案を出す |
| 区分を決められない | 上のどれにも当たらない | エージェントが本文を見て判断する。決めかねるなら、このまま聞く。多い場合は Approve for me を、注意つきで紹介する |

- 区分は、コマンドの文面から決める（利用者の発話ではないので、変えない設計の「発話を正規表現で分類しない」には当たらない）。区分の表は、出典（Kiro の deny の例、phx:permissions の RED、Claude の「任意のコードの実行」）とともに地図の隣に置く。

### 6.3 規則の確かめ方

- 観測したコマンドをすべて覆う、最も狭い段を選ぶ。`*` だけの規則、`sudo` や `env` で始まる規則の2語・1語の段（Kiro も出さない）、引数まで絞る規則（Claude が壊れやすいとする）は出さない。
- スクリプトが、Kiro の文書に書かれた当たり方（`*` は任意の文字の並び、つないだコマンドは分けて判定）で、案の規則を試す。観測したコマンドには当たり、危険な変形（例：`git push --force`、`rm -rf /`）には当たらないことを確かめ、案に「当たる例」と「当たらない例」を添える（Codex の `match`／`not_match` と同じ考え）。
- deny の案には、当たらない書き方が残ること（例：`git -C . push`）を添える。deny は守りの境界ではない。

### 6.4 案の書き方

- いちばん手間の少ない反映は、「次にこの承認画面が出たら、Always allow を選び、段は『2語＋*』、範囲は『This workspace』を選ぶ」である。ファイルを編集しなくて済む。
- チームで共有したいときは、リポジトリの中のエージェント定義の `permissions` に書く案を、文面つきで示す。ワークスペースの規則は利用者ごとで、リポジトリに入らないことを添える。
- 採用した後は、その承認の回数が減ったかを、今の「採用した案の追跡」で数える（対象の型 `approval:<見出し>`）。

## 7. 変えない設計との関係、既存の導入先への影響

- 記録は変えない（承認はログから読む）。地図の見つけ方、点検の時期、承認の回数、規則の当たり方は、スクリプトが決める。LLM は、区分を決められない承認の判断と、案の文章に使う。
- 振り返りのエージェントはハーネスを変えない。権限の規則も、案として示すだけである。
- 骨組みの検証値に `D7` と `approval:` の型が入るので、骨組みを版6にする。版5までのレポートは、作ったときの版の規則で検査する（今の `as_version` の仕組み）。
- 新しい設定（`audit_days`、`approval_min`）は既定値をコードに持つ。`config.json` の書き換えは要らない。専用エージェントの権限は変えない。
- 見込みは「`--update` だけで済む」。

## 8. 未確認のこと・決めてほしいこと

未確認：

- 承認画面で「Allow」「Always allow」を選んだときの `selectedOption` の値（CLI 3.3.0 で見たのは、拒否と取り消しと、承認した後の中断だけ）。
- 既定の権限（Approve for me を使わないとき）で、読み取りだけのコマンドに承認画面が出るか。
- Kiro の規則の当たり方の細部（文字列全体に当たるのか、先頭に当たるのか）。文書の例（`npm test *`）からは全体に当たると読んだ。
- PermissionRequest のフックを足したとき、3.4 より前の Kiro でフックの定義全体が読まれなくならないか（足すなら、実機で確かめてから）。
- memories の保存場所と、ログでの形。
- OpenAI の harness engineering の記事の本文。

決めてほしいこと：

1. 地図は、エージェントの作業に関わる項目に絞る（LICENSE などは入れない）。点数は付けない。この方針でよいか。
2. 点検の回の条件（前回から28日、または前回のレポートに型も案も無い）と、1回に1項目でよいか。
3. 承認の案の目安（3回以上、2セッション以上）と、危険の区分の出し方（外に出る操作は許可を出さない、取り返しのつかない操作は deny を勧める）でよいか。
4. 承認の案の反映は「Always allow で、この段と This workspace を選ぶ」を主にし、エージェント定義に書く文面も添える、でよいか。
5. 地図の更新を、月1回の定期実行（このリポジトリで調査し、PR にする）にするか。
6. 進め方：承認の提案（段A）と、地図と点検（段B）を、別々の PR にしてよいか。段A は、実機で承認の記録の形を1回確かめてから（承認画面で Allow と Always allow を選ぶだけ）。
