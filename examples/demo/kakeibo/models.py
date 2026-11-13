"""支出1件の形と、入力の検査。"""
from dataclasses import asdict, dataclass
import datetime

CATEGORIES = ("食費", "日用品", "交通", "住居", "娯楽", "その他")


class InvalidEntry(ValueError):
    """入力が家計簿の決まりに合わない。"""


@dataclass(frozen=True)
class Entry:
    date: str       # YYYY-MM-DD
    category: str   # CATEGORIES のいずれか
    amount: int     # 円。整数
    memo: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def make_entry(date: str, category: str, amount: int, memo: str = "") -> Entry:
    """検査してから Entry を作る。"""
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        raise InvalidEntry("日付は YYYY-MM-DD で指定してください: %s" % date) from None
    if category not in CATEGORIES:
        raise InvalidEntry("カテゴリは %s のいずれかです: %s" % ("、".join(CATEGORIES), category))
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise InvalidEntry("金額は整数の円で指定してください: %r" % (amount,))
    if amount <= 0:
        raise InvalidEntry("金額は1円以上で指定してください: %d" % amount)
    return Entry(date=date, category=category, amount=amount, memo=memo.strip())
