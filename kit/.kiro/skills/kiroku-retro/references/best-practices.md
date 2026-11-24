# ハーネスのベストプラクティス集

<!-- kiroku-best-practices:1 -->

kiroku が配る、プロジェクトに依らないハーネス（決まり、hook、skill、エージェントの設定、権限、テストや検査の仕組み）のベストプラクティスです。
振り返りのたびに、集約（`aggregate.py` の「ベストプラクティス集との照らし合わせ」）が、項目ごとに、このプロジェクトの今の状態を決めます。判定は `scripts/practices.py` が項目の ID ごとに持ち、ファイルと記録だけから決めます。同じ入力からは、いつも同じ結果になります。

- 状態は「満たしている」「満たしていない」「当てはまらない」「未確認」のどれかです。当てはまって「満たしていない」項目が、案の候補（`practice:<ID>`）です。「未確認」は、スクリプトでは決められない項目で、振り返りのエージェントが会話と文書から判断します。
- この集の文面は、レポートに引用しません。項目は `practice:<ID>` と集の版で指します。集は更新で文面が変わるので、引用すると以前のレポートの照合が崩れるためです。
- 振り返りのエージェントは、この集を web で調べ直しません。更新は、利用者が頼んだときに kiroku のリポジトリで行います。出典を読み直し、各項目の確認日と集の版を上げて、`install --update` で配ります。
- 項目の ID は変えません。消した ID は使い回しません。
- 当てはまる条件と満たした証拠は、どのプロジェクトにも使える規則で書きます。特定のプロジェクトの事情は書きません。

## BP-G1. 毎回読ませる決まりがある

- 何のためか：プロジェクトの前提と、作り方・確かめ方の決まりを、毎回の作業の始めに渡すため。言い換え：Kiro に、いつも守ってほしいことを書いたファイル（steering、AGENTS.md）があること。
- 当てはまる条件：常に。
- 満たした証拠：AGENTS.md（どの階層でも）か、常時読み込み（`inclusion: always`、または指定なし）の steering がある。
- 案の形：steering（前提、技術、構成のような基礎のファイル）か AGENTS.md。コードを読めば分かることは書かない。
- 聞き方：「毎回説明していることを、Kiro がいつも読むファイルに書いておけます。使いますか？」 a 使いたい → 案を出す／b 要らない → 出さない／c わからない → 様子見／d その他。
- 出典：
  - [Kiro, Steering](https://kiro.dev/docs/steering/)（2026-10-21 更新）：CLI V3 は always、fileMatch、manual、auto の4つの読み込みの条件を持つ。AGENTS.md は条件を持たず、常に読み込まれる。
  - [Claude Code, Best practices](https://code.claude.com/docs/en/best-practices)：リポジトリを読んでも分からないことだけを書く。短く保つ。
  - [Codex, AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)：作業の前に AGENTS.md を読む。
- 確認日：2026-11-24

## BP-S1. テストの実行方法が決まっていて、kiroku が結果を取り込める

- 何のためか：エージェントが、変更が正しいかを自分で確かめられるようにするため。結果を取り込めれば、振り返りで失敗の型を数えられる。言い換え：テストを1つのコマンドで動かせて、通ったか落ちたかが記録に残ること。
- 当てはまる条件：テストのファイルか、テストを実行する手段（package.json の test、Makefile の test、テスト用のスクリプト）がある。
- 満たした証拠：kiroku のテストの記録設定（`config.json` の `tests`）がある。
- 案の形：テストを1つのコマンドにまとめ、結果をファイル（JUnit XML など）に書かせる。記録設定は `doctor.py` で確かめる。
- 聞き方：「テストの結果を、kiroku が数えられるようにできます。テストを1つのコマンドで動かしていますか？」 a している（コマンドを教えてください）→ 記録設定の案を出す／b していない → テストのコマンドを決める案を出す／c わからない → 様子見／d その他。
- 出典：
  - [Claude Code, Best practices](https://code.claude.com/docs/en/best-practices)：「Give Claude something it can run that comes back pass or fail.」
  - [Anthropic, Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)（2025-11-26）：テストの道具を渡すと、性能が大きく上がった。
  - [kodustech/agent-readiness](https://github.com/kodustech/agent-readiness)：基準 `test-script`（package.json、Makefile などのテストのコマンド）。
- 確認日：2026-11-24

## BP-G2. 決まりに、テストの実行方法が書いてある

- 何のためか：エージェントが、正しいコマンドでテストを動かすため。違うコマンドを試して失敗する手間を無くす。言い換え：いつも読むファイルに「テストはこのコマンドで動かす」と書いてあること。
- 当てはまる条件：BP-S1 を満たす。
- 満たした証拠：常時読み込みの決まり（AGENTS.md か、常時読み込みの steering）に、テストの記録設定（`match`）に当たる行がある。
- 案の形：常時読み込みの決まりに、テストのコマンドを1行書く。古いコマンドが書かれていれば直す。
- 聞き方：「テストのコマンドを、Kiro がいつも読むファイルに書いておけます。書いてよいですか？」 a 書きたい → 案を出す／b 要らない → 出さない／c わからない → 様子見／d その他。
- 出典：
  - [Codex, AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)：例「Always run `npm test` after modifying JavaScript files.」
  - [Claude Code, Best practices](https://code.claude.com/docs/en/best-practices)：通ったか落ちたかを返すものを渡す。
- 確認日：2026-11-24

## BP-S2. 作業の終わりか保存時に、検査を動かす hook がある

- 何のためか：決まりを読んで守るのを待たず、毎回、機械的に確かめるため。言い換え：作業の終わりやファイルの保存のときに、テストや lint が自動で動く設定があること。
- 当てはまる条件：BP-S1 を満たす。
- 満たした証拠：kiroku 以外の hook のうち、トリガーが作業の終わり（Stop）、保存時（File Save）、ファイルの作成時、タスクの後のどれかで、呼ぶコマンドがテストの記録設定（`match`）か、lint・型検査・整形の道具に当たるもの。
- 案の形：作業の終わりにテストを動かし、失敗をエージェントに返す hook。保存時の lint。
- 聞き方：「作業の終わりに、テストを自動で動かす設定（hook）を使えます。使いますか？」 a 使いたい → 案を出す／b 使わないと決めている → 出さない／c わからない → 様子見／d その他。
- 出典：
  - [Kiro, Hooks のトリガー](https://kiro.dev/docs/hooks/types/)（2026-11-18 更新）：Agent Stop の用途に「code compilation, testing, formatting」、File Save に「Run linting and formatting」。
  - [Claude Code, Best practices](https://code.claude.com/docs/en/best-practices)：「If something has to happen every time, make it a hook, a deny rule, or a managed setting.」
  - [Böckeler, Harness engineering](https://martinfowler.com/articles/harness-engineering.html)（2026-04-02）：ハーネスは案内（guides）と検査（sensors）の組。検査は早い段階に置く（keep quality left）。
- 確認日：2026-11-24

## BP-S3. lint や型検査の設定がある

- 何のためか：書き方の誤りや型の誤りを、人のレビューより先に機械で見つけるため。言い換え：書き方の誤りを自動で見つける道具（lint）の設定があること。
- 当てはまる条件：プログラムのコードがある。
- 満たした証拠：lint・型検査の設定ファイル（ruff、flake8、pylint、mypy、ESLint、Biome、TypeScript、golangci-lint、RuboCop など。どの階層でも）。
- 案の形：使っている言語の lint の設定を足す。足したら、保存時の hook（BP-S2）かコミットの前の検査（BP-S4）で動かす。
- 聞き方：「書き方の誤りを自動で見つける道具（lint）を使えます。使いますか？」 a 使いたい → 案を出す／b 使わないと決めている → 出さない／c わからない → 様子見／d その他。
- 出典：
  - [kodustech/agent-readiness](https://github.com/kodustech/agent-readiness)：基準 `linter`、`formatter`。
  - [Factory, Agent Readiness](https://factory.com/news/agent-readiness)（2026-01-20）：柱の1つが Style & Validation。
- 確認日：2026-11-24

## BP-S4. コミットの前の検査がある

- 何のためか：誤りを、CI より早く、コミットの前に知らせるため。言い換え：コミットするときに、自動でテストや lint が動く設定（pre-commit）があること。
- 当てはまる条件：BP-S1 か BP-S3 を満たす。
- 満たした証拠：pre-commit の設定（`.pre-commit-config.yaml`、husky、lefthook、実行できる `.git/hooks/pre-commit`）。
- 案の形：pre-commit で、速い検査（lint、変えたファイルのテスト）を動かす。
- 聞き方：「コミットの前に、自動で検査を動かす設定（pre-commit）を使えます。使いますか？」 a 使いたい → 案を出す／b 使わないと決めている → 出さない／c わからない → 様子見／d その他。
- 出典：
  - [Factory, Agent Readiness](https://factory.com/news/agent-readiness)（2026-01-20）：「Missing pre-commit hooks mean the agent waits ten minutes for CI feedback instead of five seconds.」
  - [kodustech/agent-readiness](https://github.com/kodustech/agent-readiness)：基準 `pre-commit-hooks`。
- 確認日：2026-11-24

## BP-S5. CI がテストを動かす

- 何のためか：手元で動かし忘れても、共有する前にテストが動くようにするため。言い換え：push したときに、別の場所で自動でテストが動くこと。
- 当てはまる条件：BP-S1 を満たし、git のリモートがある。手元だけで完結するプロジェクト（リモートが無い）には当てはまらない。
- 満たした証拠：CI の定義（GitHub Actions、GitLab CI、CircleCI、Azure Pipelines、Buildkite、Jenkinsfile）の、コメントを除いた行に、テストの記録設定（`match`）に当たる行がある。
- 案の形：CI の定義に、テストのコマンドを足す。
- 聞き方：「push したときに、テストを自動で動かす設定（CI）を使えます。使いますか？」 a 使いたい → 案を出す／b 使わないと決めている → 出さない／c わからない → 様子見／d その他。
- 出典：
  - [kodustech/agent-readiness](https://github.com/kodustech/agent-readiness)：基準 `ci-runs-tests`。
- 確認日：2026-11-24

## BP-P1. 取り返しのつかない操作を止める規則がある

- 何のためか：消す、履歴を書き換える、管理者の権限で動かす、といった戻せない操作を、確認の画面や判断に頼らず止めるため。言い換え：危ないコマンドを、必ず止める設定（deny 規則）があること。
- 当てはまる条件：常に。
- 満たした証拠：リポジトリの中のエージェント定義（`.kiro/agents/*.json`、kiroku 自身のものを除く）の `permissions` の deny 規則が、例のコマンド（`rm -rf build`、`sudo apt-get install x`、`git push --force origin main`、`git reset --hard HEAD~1`）すべてに当たる。ワークスペースとユーザー共通の規則はリポジトリの外にあるので見ない。当たらないものがあれば「未確認」。
- 案の形：deny 規則（ワークスペースの規則、またはエージェント定義の `permissions`）。実行前の hook で止める方法もある。
- 聞き方：「消す・履歴を書き換えるなど、戻せない操作を必ず止める規則を、ほかの場所（ユーザー共通の設定など）で決めていますか？」 a 決めている → 出さない／b 決めていない、決めたい → 案を出す／c わからない → 様子見／d その他。
- 出典：
  - [Kiro, Permissions](https://kiro.dev/docs/permissions/)（2026-11-18 更新）：deny の例に `rm -rf *`、`sudo *`。判定は deny > ask > allow > evaluate。
  - [Claude Code, Best practices](https://code.claude.com/docs/en/best-practices)：deny 規則は、どの権限のモードでも効く。
  - [oliver-kriska/claude-elixir-phoenix](https://github.com/oliver-kriska/claude-elixir-phoenix) の `phx:permissions`（2026-11-20 に確認）：`git push --force`、`git reset --hard` を、自動で許可しない操作に分ける。
- 確認日：2026-11-20

## BP-P2. 毎回承認している操作が、規則になっている

- 何のためか：毎回同じ承認をする手間を無くし、承認の画面を、本当に確かめたい操作だけにするため。言い換え：毎回「許可」を押している安全な操作を、自動で通す設定にしてあること。
- 当てはまる条件：常に。
- 満たした証拠：承認の繰り返しの候補（別々のセッションで繰り返し承認し、拒否が無く、危険な区分に入らない操作）が無い。
- 案の形：承認画面の Always allow（範囲を狭く選ぶ）、またはワークスペースの規則。
- 聞き方：「この操作を、毎回確かめたいですか。それとも自動で通してよいですか？」 a 自動で通してよい → 案を出す／b 毎回確かめたい → 出さない／c わからない → 様子見／d その他。
- 出典：
  - [Kiro, Permissions](https://kiro.dev/docs/permissions/)（2026-11-18 更新）：承認画面の Always allow は、段（完全一致、2語＋`*`、1語＋`*`）と範囲を選んで規則を保存する。
  - [Claude Code, Commands](https://code.claude.com/docs/en/commands)（2026-11-20 に確認）：`/fewer-permission-prompts` は、会話の記録から、よく使う読み取りだけの呼び出しの許可の一覧を作る。
  - [Codex, Rules](https://learn.chatgpt.com/docs/agent-configuration/rules)（2026-11-20 に確認）：承認を求めたときに、規則の案を出すことがある。
- 確認日：2026-11-20

## BP-P3. 秘密のファイルの読み取りを止めている

- 何のためか：鍵や認証の情報を、エージェントが読んだり、会話や記録に写したりしないようにするため。言い換え：パスワードや鍵を書いたファイルを、Kiro が読めない設定にしてあること。
- 当てはまる条件：リポジトリに、秘密らしいファイル（`.env`、`.env.*`（例のファイルを除く）、`*.pem`、`*.key`）がある。
- 満たした証拠：リポジトリの中のエージェント定義の `permissions` の、読み取り（`fs_read`）の deny 規則が、見つけたファイルすべてに当たる。ワークスペースとユーザー共通の規則は見ない。当たらないものがあれば「未確認」。
- 案の形：読み取りの deny 規則（例：`**/.env`、`**/*.pem`）。
- 聞き方：「鍵やパスワードのファイル（`.env` など）を、Kiro が読めないようにする規則を決めていますか？」 a 決めている → 出さない／b 決めていない、決めたい → 案を出す／c わからない → 様子見／d その他。
- 出典：
  - [Kiro, Permissions](https://kiro.dev/docs/permissions/)（2026-11-18 更新）：deny の例に `**/.env`、`**/.env.*`、`**/*.pem`。
  - [Kiro, Steering](https://kiro.dev/docs/steering/)（2026-10-21 更新）：決まりのファイルに、鍵やパスワードを書かない。
- 確認日：2026-11-24

## BP-G3. 決まりを、条件付きの読み込みに分けている

- 何のためか：いつも読ませる決まりを、必要な分だけに保つため。長いと、大事な決まりが埋もれる。言い換え：特定のファイルを触るときだけ読む決まりに、分けてあること。
- 当てはまる条件：常時読み込みの決まりの合計が、目安（8,000 文字。最初の値）を超えている。
- 満たした証拠：条件付き（fileMatch、auto、manual）の steering がある。
- 案の形：1つの領域に1つのファイルにし、特定の場面の決まりを fileMatch や auto に移す。
- 聞き方：「いつも読ませる決まりが長くなっています。特定のファイルを触るときだけ読む形に分けられます。分けますか？」 a 分けたい → 案を出す／b このままでよい → 出さない／c わからない → 様子見／d その他。
- 出典：
  - [Kiro, Steering](https://kiro.dev/docs/steering/)（2026-10-21 更新）：読み込みの条件。「Keep Files Focused - One domain per file」。
  - [Claude Code, Best practices](https://code.claude.com/docs/en/best-practices)：「Keep CLAUDE.md short」。
- 確認日：2026-11-24

## BP-G4. 繰り返す手順が skill になっている

- 何のためか：何度も説明している手順を、毎回説明しなくて済むようにするため。言い換え：決まった手順をまとめて、名前で呼び出せるようにしてあること。
- 当てはまる条件：会話からの型の候補がある。
- 満たした証拠：kiroku 以外の skill がある（プロジェクト内か、ユーザー共通）。その skill が型に関わるかは、振り返りのエージェントが読んで決める。
- 案の形：確かめ方つきの skill。
- 聞き方：「何度も説明している手順を、名前で呼び出せる手順（skill）にまとめられます。まとめますか？」 a まとめたい → 案を出す／b 要らない → 出さない／c わからない → 様子見／d その他。
- 出典：
  - [Kiro, Agent Skills](https://kiro.dev/docs/skills/)（2026-11-18 更新）：必要なときに読み込む、手順の包み。繰り返す作業の流れに使う。
- 確認日：2026-11-24

## BP-R1. 大きな変更を、spec や計画として残している

- 何のためか：要件、設計、作業の分け方を書いてから作り、途中で前提を見失わないようにするため。言い換え：大きな機能を作るときに、計画のファイルを残すこと。
- 当てはまる条件：スクリプトでは決められない（大きな変更があったかは、振り返りのエージェントが会話から判断する）。
- 満たした証拠：`.kiro/specs/` にファイルがある。無ければ「未確認」。
- 案の形：大きな機能の前に spec（要件、設計、作業）を作る手順。
- 聞き方：「大きな機能を作るときに、先に計画のファイル（spec）を作る方法があります。使いますか？」 a 使いたい → 案を出す／b 要らない → 出さない／c わからない → 様子見／d その他。
- 出典：
  - [Kiro, Specs](https://kiro.dev/docs/specs/)（2026-11-12 更新）：spec は3つのファイル（要件、設計、作業）を作る。置き場所の `.kiro/specs` は [Permissions](https://kiro.dev/docs/permissions/) に出る。
  - [Anthropic, Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)（2025-11-26）：進み具合のファイルと git の履歴を残し、次のセッションが続きから始められるようにする。
- 確認日：2026-11-24

## BP-R2. セッションの始めに、状態や前提を渡す

- 何のためか：作業の続きや今の状態を、毎回のセッションの始めに説明し直さなくて済むようにするため。言い換え：会話を始めたときに、今の状態を自動で Kiro に渡す設定があること。
- 当てはまる条件：スクリプトでは決められない（始めに毎回、状態を説明し直しているかは、振り返りのエージェントが会話から判断する）。
- 満たした証拠：kiroku 以外の、セッション開始時（SessionStart）の hook がある。無ければ「未確認」。
- 案の形：進み具合のファイルと、それを読むセッション開始時の hook。状態は steering に書き足さない。
- 聞き方：「会話を始めるたびに、今の状態を自動で渡す設定（hook）を使えます。使いますか？」 a 使いたい → 案を出す／b 要らない → 出さない／c わからない → 様子見／d その他。
- 出典：
  - [Kiro, Hooks のトリガー](https://kiro.dev/docs/hooks/types/)（2026-11-18 更新）：Session Start の用途に「Inject workspace-specific instructions into the agent's context」。
  - [Anthropic, Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)（2025-11-26）：進み具合のファイル（claude-progress.txt）と git の履歴を、セッションの始めに読む。
- 確認日：2026-11-24

## BP-E1. 依存を固定している

- 何のためか：いつ、どこで動かしても、同じ版の依存で動くようにするため。言い換え：使う部品の版を、ファイルに固定してあること。
- 当てはまる条件：依存を定義するファイル（package.json、pyproject.toml、requirements.txt、go.mod、Cargo.toml、Gemfile、composer.json、Pipfile）に、依存が書かれている。
- 満たした証拠：そのファイルと同じフォルダに、ロックファイル（package-lock.json、yarn.lock、pnpm-lock.yaml、poetry.lock、uv.lock、Pipfile.lock、go.sum、Cargo.lock、Gemfile.lock、composer.lock など）がある。requirements.txt は、すべての行を `==` で固定していれば満たす。
- 案の形：ロックファイルを作り、コミットする。
- 聞き方：「使う部品の版を、ファイルに固定できます。固定しますか？」 a 固定したい → 案を出す／b 要らない → 出さない／c わからない → 様子見／d その他。
- 出典：
  - [kodustech/agent-readiness](https://github.com/kodustech/agent-readiness)：基準 `lock-file`（いちばん下の段）。
- 確認日：2026-11-24

## 参考（判定しない）

- memories：Kiro は、会話をまたいで、決まりや訂正を覚える（[Kiro, Memory](https://kiro.dev/docs/memory/)、2026-11-18 更新、2026-11-20 に確認）。一貫して守らせたいことは steering に書く、とある。保存場所とログでの形を確かめていないので、項目にしない。
