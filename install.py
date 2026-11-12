#!/usr/bin/env python3
"""共通インストーラー。POSIX shell と PowerShell から同じ処理を呼ぶ。"""
import argparse
from pathlib import Path
import shutil


def install(target, update=False, python_command="python3"):
    target = Path(target).resolve()
    if not target.is_dir():
        raise ValueError("導入先が見つかりません: %s" % target)
    kit = Path(__file__).resolve().parent / "kit"
    files = {}
    for source in sorted(kit.rglob("*")):
        if not source.is_file() or "__pycache__" in source.parts:
            continue
        relative = source.relative_to(kit)
        content = source.read_text(encoding="utf-8")
        # フック・権限規則・振り返り手順のランナーを一致させる。
        if source.suffix in (".json", ".md"):
            content = content.replace("python3 .kiro/", python_command + " .kiro/")
        files[relative] = content
    config = Path(".kiro/kiroku/config.json")
    conflicts = [str(p) for p, content in files.items()
                 if p != config and (target / p).exists() and not update
                 and (target / p).read_text(encoding="utf-8") != content]
    if conflicts:
        raise ValueError("既存ファイルと衝突しています。変更していません: " + ", ".join(conflicts))
    for relative, content in files.items():
        path = target / relative
        if relative == config and path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    old_data, data = target / ".kiro/retro", target / ".kiro/kiroku/data"
    if old_data.is_dir() and not data.exists():
        shutil.move(str(old_data), str(data))
    if update:
        for name in ("record.py", "aggregate.py", "excerpt.py"):
            old = target / ".kiro/kiroku" / name
            if old.is_file():
                old.unlink()
    ignore = target / ".gitignore"
    content = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    lines = content.splitlines()
    lines = [".kiro/kiroku/data/" if line == ".kiro/retro/" else line for line in lines]
    if ".kiro/kiroku/data/" not in lines:
        lines.extend(["# kiroku の生データ。コミットしない。", ".kiro/kiroku/data/"])
    ignore.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--python-command", choices=("python3", "python", "py -3"), default="python3")
    args = parser.parse_args()
    try:
        install(args.target, args.update, args.python_command)
    except (OSError, ValueError) as exc:
        parser.exit(1, "%s\n" % exc)
    print("完了。テスト結果の記録は .kiro/kiroku/config.json の tests を設定してください。")


if __name__ == "__main__":
    main()
