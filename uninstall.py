#!/usr/bin/env python3
"""kiroku を導入先から取り除く。POSIX shell と PowerShell から同じ処理を呼ぶ。

  python3 uninstall.py <導入先>                  何を残すかを聞いてから消す
  python3 uninstall.py <導入先> --all            すべて消す
  python3 uninstall.py <導入先> --keep-reports   レポートだけ残す
  --dry-run  消すものを表示するだけ    --yes  確認を省く

消すのは kiroku が置いたものだけ。steering やほかの hooks、skills、agents など、
kiroku を使って改善したハーネスには触らない。
"""
import argparse
from pathlib import Path
import shutil
import sys

# kiroku が置くもの。旧版の置き場所も含む。
OWNED = (
    ".kiro/hooks/kiroku.json",
    ".kiro/agents/kiroku-retro.json",
    ".kiro/skills/kiroku-retro",
    ".kiro/kiroku",
    ".kiro/retro",
)
REPORTS = Path(".kiro/kiroku/data/reports")
IGNORE_LINES = (".kiro/kiroku/data/", ".kiro/retro/")
IGNORE_COMMENT = "# kiroku の生データ"
# 中身が無くなったら消すディレクトリ。深いほうから順に見る。
MAYBE_EMPTY = (".kiro/kiroku/data", ".kiro/kiroku", ".kiro/hooks", ".kiro/agents", ".kiro/skills", ".kiro")


def plan(target, keep_reports):
    """消すものと残すものを決める。まだ何も消さない。"""
    target = Path(target).resolve()
    if not target.is_dir():
        raise ValueError("導入先が見つかりません: %s" % target)
    remove, keep = [], []
    reports = target / REPORTS
    has_reports = reports.is_dir() and any(reports.iterdir())
    for name in OWNED:
        path = target / name
        if not path.exists() and not path.is_symlink():
            continue
        if name == ".kiro/kiroku" and keep_reports and has_reports:
            # レポート以外を1つずつ消す。
            for child in sorted(path.iterdir()):
                if child.name != "data":
                    remove.append(child)
            data = path / "data"
            if data.is_dir():
                remove.extend(c for c in sorted(data.iterdir()) if c.name != "reports")
            keep.append(reports)
        else:
            remove.append(path)
    return target, remove, keep


def clean_gitignore(target, dry_run):
    """.gitignore から kiroku の行を外す。外したら True。"""
    ignore = target / ".gitignore"
    if not ignore.is_file():
        return False
    lines = ignore.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines
            if line.strip() not in IGNORE_LINES and not line.startswith(IGNORE_COMMENT)]
    if kept == lines:
        return False
    if not dry_run:
        if any(line.strip() for line in kept):
            ignore.write_text("\n".join(kept) + "\n", encoding="utf-8")
        else:
            ignore.unlink()  # kiroku の行しか無かった
    return True


def uninstall(target, keep_reports=False, dry_run=False):
    target, remove, keep = plan(target, keep_reports)
    if not dry_run:
        for path in remove:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
        for name in MAYBE_EMPTY:
            directory = target / name
            if directory.is_dir() and not directory.is_symlink() and not any(directory.iterdir()):
                directory.rmdir()
    # レポートを残すなら、git に載らないよう .gitignore の行も残す。
    ignore_changed = False if keep else clean_gitignore(target, dry_run)
    return {"removed": [str(p.relative_to(target)) for p in remove],
            "kept": [str(p.relative_to(target)) for p in keep],
            "gitignore": ignore_changed}


def ask_mode():
    print("何を残しますか？")
    print("  1) すべて消す（記録、振り返りノート、台帳、レポート、設定を含む）")
    print("  2) レポートだけ残す（.kiro/kiroku/data/reports/）")
    print("  3) やめる")
    answer = input("番号を入力してください [3]: ").strip()
    return {"1": "all", "2": "keep"}.get(answer)


def main(argv=None):
    parser = argparse.ArgumentParser(description="kiroku を導入先から取り除く")
    parser.add_argument("target")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true", help="すべて消す")
    mode.add_argument("--keep-reports", action="store_true", help="レポートだけ残す")
    parser.add_argument("--dry-run", action="store_true", help="消すものを表示するだけで、消さない")
    parser.add_argument("--yes", action="store_true", help="確認を省く")
    args = parser.parse_args(argv)

    if args.all or args.keep_reports:
        keep_reports = args.keep_reports
    elif sys.stdin.isatty():
        chosen = ask_mode()
        if chosen is None:
            print("何も変更していません。")
            return 0
        keep_reports = chosen == "keep"
    else:
        parser.exit(2, "--all か --keep-reports を指定してください。\n")

    try:
        preview = uninstall(args.target, keep_reports, dry_run=True)
    except (OSError, ValueError) as exc:
        parser.exit(1, "%s\n" % exc)
    if not preview["removed"] and not preview["gitignore"]:
        print("kiroku のファイルは見つかりませんでした。何も変更していません。")
        return 0
    print("消すもの：")
    for name in preview["removed"]:
        print("  %s" % name)
    if preview["gitignore"]:
        print("  .gitignore の kiroku の行")
    if preview["kept"]:
        print("残すもの：")
        for name in preview["kept"]:
            print("  %s（.gitignore の行も残します）" % name)
    elif keep_reports:
        print("レポートはありませんでした。")
    print("steering やほかの hooks、skills、agents には触りません。")
    if args.dry_run:
        print("--dry-run のため、何も変更していません。")
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            parser.exit(2, "確認できないため中止しました。--yes を付けてください。\n")
        if input("消してよいですか？ [y/N]: ").strip().lower() not in ("y", "yes"):
            print("何も変更していません。")
            return 0
    try:
        uninstall(args.target, keep_reports)
    except OSError as exc:
        parser.exit(1, "途中で失敗しました: %s\n" % exc)
    print("取り除きました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
