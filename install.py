#!/usr/bin/env python3
"""共通インストーラー。POSIX shell と PowerShell から同じ処理を呼ぶ。"""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil


def default_config():
    """スクリプトが持つ設定の既定値。config.json に無い項目は、この値で動く。"""
    path = Path(__file__).resolve().parent / "kit/.kiro/skills/kiroku-retro/scripts/record.py"
    spec = importlib.util.spec_from_file_location("kiroku_record", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DEFAULT_CONFIG, module.ADDITIVE_KEYS


def config_notes(target):
    """導入先の config.json について、知らせておくことを返す。"""
    defaults, additive = default_config()
    try:
        custom = json.loads((Path(target) / ".kiro/kiroku/config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["config.json を読めません。既定値で動きます。"]
    if not isinstance(custom, dict):
        return ["config.json の形が不正です。既定値で動きます。"]
    notes = []
    missing = [k for k in defaults if k not in custom]
    if missing:
        notes.append("config.json に無い項目は、既定値で動きます（書き足す必要はありません）：")
        notes += ["  %s = %s" % (k, json.dumps(defaults[k], ensure_ascii=False)) for k in missing]
    for key in additive:
        if key in custom:
            notes.append("config.json の %s は、既定値に足して使います。既定値は常に有効です。" % key)
    return notes


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
    changes = {"新規": [], "更新": [], "変更なし": [], "そのまま（導入先の設定）": []}
    for relative, content in files.items():
        path = target / relative
        if relative == config and path.exists():
            changes["そのまま（導入先の設定）"].append(str(relative))
            continue
        if not path.exists():
            changes["新規"].append(str(relative))
        elif path.read_text(encoding="utf-8") != content:
            changes["更新"].append(str(relative))
        else:
            changes["変更なし"].append(str(relative))
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
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--python-command", choices=("python3", "python", "py -3"), default="python3")
    args = parser.parse_args()
    try:
        changes = install(args.target, args.update, args.python_command)
    except (OSError, ValueError) as exc:
        parser.exit(1, "%s\n" % exc)
    for label, names in changes.items():
        if label == "変更なし":
            continue
        for name in names:
            print("%s  %s" % (label, name))
    print("新規 %d、更新 %d、変更なし %d" % (
        len(changes["新規"]), len(changes["更新"]), len(changes["変更なし"])))
    if not changes["新規"] and not changes["更新"]:
        print("導入先は、この kiroku と同じ内容です。")
    if changes["そのまま（導入先の設定）"]:
        for line in config_notes(args.target):
            print(line)
    print("完了。テスト結果の記録は .kiro/kiroku/config.json の tests を設定してください。")


if __name__ == "__main__":
    main()
