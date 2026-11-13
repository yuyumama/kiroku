"""比較対象の所在と、steering の重さを出す。設定本文・秘密値は集約出力に複製しない。"""
import re
import os
from pathlib import Path


def inventory(root):
    home = os.environ.get("KIRO_HOME") or os.path.join(os.path.expanduser("~"), ".kiro")
    result = []
    for scope, base in (("project", Path(root) / ".kiro"), ("user", Path(home))):
        for name in ("steering", "hooks", "skills", "agents", "settings/mcp.json"):
            path = base / name
            row = {"scope": scope, "path": str(path), "status": "present", "files": []}
            try:
                path.stat()
                if path.is_file():
                    row["files"] = [str(path)]
                else:
                    def onerror(error):
                        row["status"] = "unreadable"
                    for directory, dirs, files in os.walk(path, onerror=onerror, followlinks=False):
                        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
                        row["files"].extend(str(Path(directory) / f) for f in sorted(files)
                                            if not f.endswith(".pyc"))
            except FileNotFoundError:
                row["status"] = "absent"
            except OSError:
                row["status"] = "unreadable"
            result.append(row)
    return result


INCLUSION = re.compile(r"^inclusion:\s*[\"']?([A-Za-z]+)", re.M)


def steering_weight(root):
    """steering と AGENTS.md の大きさと、読み込みの条件を出す。

    常時読み込みが増え続けると、既存の決まりが埋もれる。足す案の前に合計を見られるようにする。
    """
    home = os.environ.get("KIRO_HOME") or os.path.join(os.path.expanduser("~"), ".kiro")
    places = [("project", Path(root) / ".kiro" / "steering"), ("user", Path(home) / "steering")]
    files = []
    for scope, base in places:
        for directory, dirs, names in os.walk(base, followlinks=False):
            dirs.sort()
            for name in sorted(names):
                if name.lower().endswith(".md"):
                    files.append((scope, Path(directory) / name))
    agents = Path(root) / "AGENTS.md"
    if agents.is_file():
        files.append(("project", agents))
    rows = []

    def display(scope, path):
        """表に出す短いパス。プロジェクト内はルートから、ユーザー共通は ~/.kiro から。"""
        base, prefix = (Path(root), "") if scope == "project" else (Path(home), "~/.kiro/")
        try:
            return prefix + path.relative_to(base).as_posix()
        except ValueError:
            return str(path)

    for scope, path in files:
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            rows.append({"scope": scope, "path": str(path), "display": display(scope, path),
                         "inclusion": "unreadable", "chars": 0})
            continue
        inclusion = "always"  # 既定。AGENTS.md は条件を指定できず、常に読み込まれる。
        if path.name != "AGENTS.md" and text.startswith("---"):
            end = text.find("\n---", 3)
            found = INCLUSION.search(text[:end]) if end != -1 else None
            if found:
                inclusion = found.group(1)
        rows.append({"scope": scope, "path": str(path), "display": display(scope, path),
                     "inclusion": inclusion, "chars": len(text)})
    always = {scope: sum(r["chars"] for r in rows if r["scope"] == scope and r["inclusion"] == "always")
              for scope in ("project", "user")}
    return {"files": rows, "always_chars": always, "always_total": sum(always.values())}
