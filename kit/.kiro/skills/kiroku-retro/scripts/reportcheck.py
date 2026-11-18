"""レポートの数値・対象の型を記録と照合する。文章の意味は判定しない。"""
import json
import re

from ledger import HEADING, TARGET


def facts(data):
    targets = {}
    for row in data['tool_failures']:
        prefix = {'tool_failure': 'tool', 'tool_denied': 'denied',
                  'tool_cancelled': 'cancelled', 'tool_timeout': 'timeout'}[row['type']]
        targets[prefix + ':' + row['command_key']] = {'count': row['count'], 'sessions': row['sessions']}
    for row in data['topics']:
        if row['candidate']:
            targets['topic:' + row['topic']] = {'count': row['count'], 'sessions': row['sessions']}
    for row in data['test_failures']:
        if row['class'] == 'regression':
            targets['test:' + row['test_id']] = {'count': row['count'], 'sessions': row['sessions']}
    plan = data['reading_plan']
    return {'since': data['since'], 'until': data['until'], 'events': data['event_count'],
            'sessions': {k: data['coverage'][k] for k in ('sessions', 'recorded', 'log_only')},
            'session_agents': {r['session_id']: r.get('agent') for r in data['sessions']},
            'unread': len(plan['read']) + plan['rest'],
            'test_runs': sum(p['test_runs'] for p in data['periods'].values()),
            'test_results_unknown': len(data['test_result_gaps']),
            'unrecorded_calls': {kind: sum(r['count'] for r in data['unrecorded_calls'] if r['kind'] == kind)
                                 for kind in ('blocked', 'interrupted')},
            'targets': targets,
            'harness': [{k: r[k] for k in ('scope', 'path', 'status')} for r in data['harness']],
            'test_configuration': data['test_configuration']}


def extract(text):
    blocks = re.findall(r'^```kiroku-facts\s*\n(.*?)^```\s*$', text, re.M | re.S)
    if len(blocks) != 1:
        raise ValueError('kiroku-facts の JSON ブロックを1個書いてください')
    value = json.loads(blocks[0])
    if not isinstance(value, dict) or not all(k in value for k in ('since', 'until')):
        raise ValueError('kiroku-facts の期間がありません')
    return value


def validate(text, expected):
    errors = []
    try:
        if extract(text) != expected:
            errors.append('検証値が集約と一致しません。件数・所在・設定を再確認してください。')
    except (ValueError, TypeError):
        errors.append('kiroku-facts を読み取れません。集約の検証値をそのまま使ってください。')
    # 引用した過去の案ではなく、今回の案に属する対象だけを検査する。
    current = False
    titles = {}
    for line in text.splitlines():
        heading = HEADING.match(line)
        if heading:
            current = True
            titles.setdefault(heading.group(1), []).append(heading.group(2).strip())
        elif line.startswith('#'):
            current = False
        elif current:
            target = TARGET.search(line)
            if target and target.group(1).strip() not in expected['targets']:
                errors.append('集約にない対象の型：' + target.group(1).strip())
    for number, names in titles.items():
        if len(names) > 1:
            # 同じ番号の案が複数あると、「P1 は採用」がどの案のことか決まらない。
            errors.append('案の番号 %s が %d 回使われています。プロジェクト内とユーザー共通を通して、'
                          'P1 からの通し番号にしてください：%s' % (number, len(names), '／'.join(names)))
    return errors
