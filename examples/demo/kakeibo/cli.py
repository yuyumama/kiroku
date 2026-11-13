"""コマンドライン。python3 -m kakeibo で起動する。"""
import argparse
import sys
from typing import List, Optional

from . import storage
from .models import CATEGORIES, InvalidEntry, make_entry
from .report import format_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kakeibo", description="家計簿")
    parser.add_argument("--file", default=storage.DEFAULT_PATH, help="家計簿のファイル")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="支出を1件記録する")
    add.add_argument("date", help="日付（YYYY-MM-DD）")
    add.add_argument("category", help="カテゴリ（%s）" % "、".join(CATEGORIES))
    add.add_argument("amount", type=int, help="金額（円）")
    add.add_argument("memo", nargs="?", default="", help="メモ")

    commands.add_parser("list", help="記録を一覧にする")

    report = commands.add_parser("report", help="月ごとに集計する")
    report.add_argument("month", help="月（YYYY-MM）")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "add":
        try:
            entry = make_entry(args.date, args.category, args.amount, args.memo)
        except InvalidEntry as error:
            print("記録できません: %s" % error, file=sys.stderr)
            return 1
        storage.add(entry, args.file)
        print("記録しました: %s %s %s円" % (entry.date, entry.category, format(entry.amount, ",")))
    elif args.command == "list":
        for entry in storage.load(args.file):
            print("%s  %s  %s円  %s" % (entry.date, entry.category, format(entry.amount, ","), entry.memo))
    elif args.command == "report":
        print(format_report(storage.load(args.file), args.month))
    return 0
