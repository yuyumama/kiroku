"""比較対象の所在だけを列挙する。設定本文・秘密値は集約出力に複製しない。"""
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
