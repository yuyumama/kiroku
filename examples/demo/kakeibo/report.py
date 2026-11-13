"""月ごとの集計。"""
from typing import Dict, List

from .models import CATEGORIES, Entry


def monthly_totals(entries: List[Entry], month: str) -> Dict[str, int]:
    """month は YYYY-MM。支出のあるカテゴリだけを、決まった順で返す。"""
    totals = {category: 0 for category in CATEGORIES}
    for entry in entries:
        if entry.date.startswith(month + "-"):
            totals[entry.category] += entry.amount
    return {category: total for category, total in totals.items() if total}


def format_report(entries: List[Entry], month: str) -> str:
    totals = monthly_totals(entries, month)
    if not totals:
        return "%s の支出はありません。" % month
    lines = ["%s の支出" % month]
    for category, total in totals.items():
        lines.append("  %s: %s円" % (category, format(total, ",")))
    lines.append("  合計: %s円" % format(sum(totals.values()), ","))
    return "\n".join(lines)
