#!/bin/bash
# run.sh <run名 例 A1> <phase 番号> [続きの発話]
B=$(cd "$(dirname "$0")" && pwd)
R=$B/runs/$1; C=${1:0:1}; P=$2
cd "$R/ws"
FLAGS=(--model sonnet --output-format json --max-turns 200)
case $C in
  A) PROMPT="このリポジトリの開発ハーネス（.kiro/ の steering、hooks、skills、agents など）の改善を提案して。ファイルは変更せず、提案は harness-proposals.md に書いて。"
     TOOLS="Read,Glob,Grep,Write,Bash";;
  B) PROMPT="このリポジトリの開発ハーネス（.kiro/ の steering、hooks、skills、agents など）の改善を提案して。ファイルは変更せず、提案は harness-proposals.md に書いて。
このプロジェクトで Kiro を使った過去のセッションのログが $R/kirohome/sessions/0123456789abcdef/ にある（セッションごとに messages.jsonl と session.json）。それも読んで、繰り返している指摘や失敗、毎回の承認などを踏まえて提案して。"
     TOOLS="Read,Glob,Grep,Write,Bash";;
  D) PROMPT="このリポジトリの開発ハーネス（.kiro/ の steering、hooks、skills、agents など）の改善を提案して。ファイルは変更せず、提案は harness-proposals.md に書いて。
このプロジェクトで Kiro を使った過去のセッションのログが $R/kirohome/sessions/0123456789abcdef/ にある（セッションごとに messages.jsonl と session.json）。それも読んで、繰り返している指摘や失敗、毎回の承認などを踏まえて提案して。
案を作る前に、記録だけでは決められないことがあれば、1回だけまとめて質問して、答えを待って。"
     TOOLS="Read,Glob,Grep,Write,Edit,Bash";;
  C) PROMPT="/kiroku-retro"
     export KIRO_HOME=$R/kirohome KIRO_SESSION_ID=sess_retro
     S=.kiro/skills/kiroku-retro/scripts
     TOOLS="Read,Glob,Grep,Write,Edit,Bash(python3 $S/aggregate.py),Bash(python3 $S/aggregate.py:*),Bash(python3 $S/excerpt.py:*),Bash(python3 $S/ledger.py:*)"
     AGENT=$(python3 -c "import json;print(json.load(open('.kiro/agents/kiroku-retro.json'))['prompt'])")
     FLAGS+=(--append-system-prompt "あなたは Kiro CLI の kiroku-retro エージェントとして動いています。$AGENT
最初に .kiro/skills/kiroku-retro/SKILL.md を読み、その手順に従います。Kiro のツールとの対応：shell は Bash、read は Read・Glob・Grep、write は Write です。");;
esac
if [ "$P" = 1 ]; then
  claude -p "$PROMPT" "${FLAGS[@]}" --allowedTools "$TOOLS" > "$R/out1.json" 2> "$R/err1.txt"
else
  SID=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['session_id'])" "$R/out$((P-1)).json")
  claude -p "$3" --resume "$SID" "${FLAGS[@]}" --allowedTools "$TOOLS" > "$R/out$P.json" 2> "$R/err$P.txt"
fi
echo "done $1 phase $P"
