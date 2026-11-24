"""比較対象の所在と、steering の重さを出す。設定本文・秘密値は集約出力に複製しない。"""
import re
import os
import subprocess
from pathlib import Path

# git が使えないときに、AGENTS.md を探さないディレクトリ。最初の値で、根拠は薄い。
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
KIROKU_DATA = ".kiro/kiroku/"
STEERING_GUIDE_CHARS = 8000  # 常時読み込みの目安。超えたら、足す前に分割や条件付きを検討する


def repo_files(root):
    """リポジトリのファイル（ルートからの相対パス、/ 区切り）。kiroku の生データは除く。

    git が使えれば、追跡しているファイルと、無視されていない未追跡のファイル。
    使えなければ、ディレクトリを辿る（SKIP_DIRS は辿らない）。
    """
    try:
        done = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
                              capture_output=True, timeout=60)
        names = done.stdout.decode("utf-8", "replace").split("\0") if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        names = None
    if names is not None:
        return sorted({n for n in names if n and not n.startswith(KIROKU_DATA)})
    found = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        relative = Path(directory).relative_to(root).as_posix()
        prefix = "" if relative == "." else relative + "/"
        if prefix.startswith(KIROKU_DATA):
            continue
        found += [prefix + name for name in files]
    return sorted(found)


def agents_files(root):
    """リポジトリ内の AGENTS.md（直下とサブディレクトリ）。Kiro はサブディレクトリのものも常に読み込む。"""
    return [Path(root) / name for name in repo_files(root) if name.split("/")[-1] == "AGENTS.md"]


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
        if scope == "project":
            # AGENTS.md はディレクトリにまとまっていないので、リポジトリ内のすべてを1つの所在として出す。
            # ユーザー共通の AGENTS.md は ~/.kiro/steering/ に置くので、steering の所在に含まれる。
            # added_in：この所在を出すようになった、レポートの形式の版。以前の版のレポートの検査では除く。
            row = {"scope": scope, "path": str(Path(root) / "AGENTS.md"), "status": "absent", "files": [],
                   "added_in": 3}
            try:
                Path(root).stat()
                steering = (Path(root) / ".kiro" / "steering").resolve()
                for path in agents_files(root):
                    try:
                        path.stat()
                    except FileNotFoundError:
                        continue   # 追跡されているが、作業ツリーから消えたもの
                    if not path.resolve().is_relative_to(steering):
                        row["files"].append(str(path))
                row["status"] = "present" if row["files"] else "absent"
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
    seen = {path.resolve() for _, path in files}
    for agents in agents_files(root):   # 直下とサブディレクトリ。steering の中にあるものは数え済み
        if agents.is_file() and agents.resolve() not in seen:
            seen.add(agents.resolve())
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
