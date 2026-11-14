#!/usr/bin/env python3
"""実行せずに、申告したテストコマンドと記録設定の一致を検査する。"""
import argparse
import json
import os
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import commands
from record import find_root, load_config, mask


def inspect(root, command):
    errors = []
    try:
        config_path = os.path.join(root, '.kiro/kiroku/config.json')
        with open(config_path, encoding='utf-8') as handle:
            custom = json.load(handle)
        if not isinstance(custom, dict) or not isinstance(custom.get('tests', []), list):
            raise ValueError('tests は配列が必要です')
        config = load_config(root)
        entry, how = commands.test_match(command, config)
    except (OSError, ValueError, TypeError, AttributeError, re.error):
        entry, how = None, None
        errors.append('テストの検出設定を読めません。config.json を確認してください。')
    if entry is None:
        errors.append('指定したコマンドが検出設定と一致しません。config.json の tests の match を確認してください。')
    elif not entry.get('report') or entry.get('format', 'junit') != 'junit':
        errors.append('取り込む JUnit レポートの場所と形式を設定してください。')
    try:
        with open(os.path.join(root, '.kiro/hooks/kiroku.json'), encoding='utf-8') as handle:
            hooks = json.load(handle)['hooks']
        if not any(h.get('name') == 'kiroku-record-test-start' and h.get('trigger') == 'PreToolUse'
                   for h in hooks):
            errors.append('実行前の記録フックがありません。kiroku を更新してください。')
    except (OSError, ValueError, TypeError, KeyError):
        errors.append('記録フックの設定を読めません。')
    notes = []
    if how == 'text':
        notes.append('起動先では照合できず、コマンドの文面で一致しました（パイプ、前置き、展開など）。'
                     '結果を取り込むのは、実行の前後で JUnit レポートが変わったときだけです。')
    return {'ok': not errors, 'command': mask(command), 'matched': entry, 'matched_by': how,
            'errors': errors, 'notes': notes,
            'verification': '設定の一致だけを確認します。実行後に JUnit と test_run の照合が必要です。'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=None)
    parser.add_argument('--command', required=True, help='普段実行するテストコマンド')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    result = inspect(args.root or find_root(os.getcwd()), args.command)
    if args.json:
        print(mask(json.dumps(result, ensure_ascii=False, indent=2)))
    else:
        print('設定の一致：%s' % ('確認済み' if result['ok'] else '要確認'))
        for line in result['errors'] + result['notes']:
            print('- ' + line)
        print(result['verification'])
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
