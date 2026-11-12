#!/usr/bin/env python3
"""追加確認用。確認A（summarize.py）のあとに、作業用ディレクトリで実行する。

使い方: python3 .kiro/kiroku-probe/followup.py

1. シェル実行の tool_response を、そのまま表示する。
   検証用の echo と ls の結果だけが対象。終了コードがどう書かれるかを見るため。
2. 確認Aのセッションのログについて、キー名と値の型だけを表示する。
   文字列の中身は出さない（type や role など、種別を表す短い値を除く）。
"""
import glob, json, os
VERB = {"type", "role", "kind", "event", "tool_name", "name", "status"}
def shape(v, k=None, d=0):
    if isinstance(v, dict):
        return {a: shape(b, a, d + 1) for a, b in v.items()} if d < 4 else "<dict>"
    if isinstance(v, list):
        return [shape(v[0], k, d + 1), "<list len=%d>" % len(v)] if v else []
    if isinstance(v, str):
        return v if k in VERB and len(v) < 40 else "<str>"
    return v
sid = None
print("## 1. シェル実行の tool_response（そのまま）")
for p in sorted(glob.glob(".kiro/kiroku-probe/out/*-PostToolUse.json")):
    pl = json.load(open(p, encoding="utf-8")).get("payload") or {}
    sid = pl.get("session_id") or sid
    if pl.get("tool_name") == "execute_bash":
        print("--- command: %s" % pl["tool_input"].get("command"))
        print(pl.get("tool_response"))
print("\n## 2. セッションログの行の形")
home = os.environ.get("KIRO_HOME") or os.path.expanduser("~/.kiro")
tail = (sid or "")[-36:]
dirs = [d for d in glob.glob(os.path.join(home, "sessions", "*", "*"))
        if tail and tail in d and os.path.isdir(d)]
print("session_id の形: %s / 一致したディレクトリ: %d 個" % (
    "".join("x" if c.isalnum() else c for c in sid or ""), len(dirs)))
for d in dirs[:1]:
    print("ディレクトリ名の形: " + "".join("x" if c.isalnum() else c for c in os.path.basename(d)))
    for f in sorted(os.listdir(d)):
        print("\n### " + f)
        path = os.path.join(d, f)
        if f.endswith(".jsonl"):
            seen = []
            for n, line in enumerate(open(path, encoding="utf-8")):
                try:
                    s = json.dumps(shape(json.loads(line)), ensure_ascii=False, sort_keys=True)
                except ValueError:
                    s = "<JSON でない行>"
                if s not in seen:
                    seen.append(s)
            print("行数: %d / 形の種類: %d" % (n + 1, len(seen)))
            for s in seen[:12]:
                print(s)
        elif f.endswith(".json"):
            print(json.dumps(shape(json.load(open(path, encoding="utf-8"))), ensure_ascii=False, sort_keys=True))
