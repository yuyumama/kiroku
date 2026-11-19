#!/usr/bin/env python3
"""kiroku の記録をゼロからやり直す。人が端末から実行する（振り返りのエージェントには許可しない）。

使い方（リポジトリのルートで）:
  python3 .kiro/skills/kiroku-retro/scripts/reset.py --dry-run   退避するものを表示するだけ
  python3 .kiro/skills/kiroku-retro/scripts/reset.py             確認してから退避する
  python3 .kiro/skills/kiroku-retro/scripts/reset.py --yes       確認を省く

行うこと:
  1. .kiro/kiroku/data/ の中身（記録、state、ノート、台帳、レポートなど）を、
     .kiro/kiroku/data/backups/<日時>/ に移す。消さない。戻すときは、そこから data/ に移し直す。
  2. 新しい state に「記録の始まり」の時刻を控える。集約は、それより前の記録と会話を数えない。
     Kiro 自身のセッションログ（~/.kiro/sessions/）は消せないし消さないので、こうしないと
     やり直す前の会話が「未読」として戻ってくる。
設定（config.json）、フック、エージェント定義、.gitignore には触らない。
"""
import argparse
import datetime
import os
import shutil
import sys

sys.dont_write_bytecode = True  # 導入先に __pycache__ を作らない
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from record import recording_lock, save_state, script_root  # noqa: E402

KEEP = ("record.lock", "backups")   # 移さないもの。ロックは記録フックと共有し、退避先はここに貯める


def plan(data_dir):
    try:
        return sorted(name for name in os.listdir(data_dir) if name not in KEEP)
    except OSError:
        return []


def reset(root, now=None):
    """退避して、記録の始まりを控える。(退避先, 移したもの) を返す。"""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    data_dir = os.path.join(root, ".kiro", "kiroku", "data")
    os.makedirs(data_dir, exist_ok=True)
    with recording_lock(data_dir):   # 記録フックが同時に書かないようにする
        moved = plan(data_dir)
        backup = os.path.join(data_dir, "backups", now.astimezone().strftime("%Y%m%d-%H%M%S"))
        suffix = 2
        while os.path.exists(backup):
            backup = backup.rsplit("~", 1)[0] + "~%d" % suffix
            suffix += 1
        os.makedirs(backup)
        for name in moved:
            shutil.move(os.path.join(data_dir, name), os.path.join(backup, name))
        save_state(data_dir, {"recording_start": now.isoformat().replace("+00:00", "Z")})
    return backup, moved


def main(argv=None):
    parser = argparse.ArgumentParser(description="kiroku の記録を退避し、ゼロからやり直す")
    parser.add_argument("--root", default=None, help="リポジトリのルート（既定は現在の git リポジトリ）")
    parser.add_argument("--dry-run", action="store_true", help="退避するものを表示するだけ")
    parser.add_argument("--yes", action="store_true", help="確認を省く")
    args = parser.parse_args(argv)
    root, refused = script_root(args.root)
    if refused:
        print(refused)
        return 1
    data_dir = os.path.join(root, ".kiro", "kiroku", "data")
    moved = plan(data_dir)
    shown = os.path.relpath(data_dir, root)
    print("退避するもの（%s の中、%d 個）：%s" % (shown, len(moved), "、".join(moved) or "なし"))
    print("設定、フック、エージェント定義、.gitignore、Kiro のセッションログには触りません。")
    if args.dry_run:
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            print("確認できません。実行するときは --yes を付けてください。")
            return 1
        if input("退避して、記録をゼロからやり直しますか？ [y/N] ").strip().lower() not in ("y", "yes"):
            print("やめました。")
            return 1
    backup, moved = reset(root)
    print("退避しました：%s" % os.path.relpath(backup, root))
    print("これより前の記録と会話は、集約で数えません。戻すときは、退避先の中身を %s に移し直します"
          "（今の state.json は上書きしてよい）。" % shown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
