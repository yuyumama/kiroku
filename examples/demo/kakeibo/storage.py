"""家計簿を JSON ファイルに保存する。"""
import json
import os
from typing import List

from .models import Entry, make_entry

DEFAULT_PATH = os.path.join("data", "kakeibo.json")


def load(path: str = DEFAULT_PATH) -> List[Entry]:
    """ファイルが無ければ空の家計簿として扱う。"""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        rows = json.load(handle)
    return [make_entry(**row) for row in rows]


def save(entries: List[Entry], path: str = DEFAULT_PATH) -> None:
    """書きかけのファイルを残さないよう、一時ファイルに書いてから置き換える。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump([entry.to_dict() for entry in entries], handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def add(entry: Entry, path: str = DEFAULT_PATH) -> List[Entry]:
    entries = load(path)
    entries.append(entry)
    entries.sort(key=lambda item: item.date)
    save(entries, path)
    return entries
