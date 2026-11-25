#!/bin/sh
# prep.sh <条件 A|B|C> <番号>
set -e
B=$(cd "$(dirname "$0")" && pwd)
R=$B/runs/$1$2
rm -rf "$R"; mkdir -p "$R"
cp -a "$B/base" "$R/ws"
cp -a "$B/kirohome" "$R/kirohome"
python3 - "$R" <<'PY'
import json, glob, sys
r = sys.argv[1]
for p in glob.glob(r + "/kirohome/sessions/*/*/session.json"):
    d = json.load(open(p)); d["workspacePaths"] = [r + "/ws"]; json.dump(d, open(p, "w"))
PY
if [ "$1" != C ]; then
  cd "$R/ws"
  rm -rf .kiro/kiroku/data .kiro/skills/kiroku-retro .kiro/agents/kiroku-retro.json .kiro/hooks/kiroku.json
  git checkout -q .gitignore
  rm -rf .kiro/kiroku
fi
[ "$1" = A ] && rm -rf "$R/kirohome"
echo "$R"
