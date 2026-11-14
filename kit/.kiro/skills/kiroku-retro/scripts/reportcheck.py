"""レポートの数値・対象の型を記録と照合する。文章の意味は判定しない。"""
import json
import re


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
    for line in text.splitlines():
        if re.match(r'^###\s+P\d+[.．:：]?\s', line):
            current = True
        elif line.startswith('#'):
            current = False
        elif current:
            target = re.search(r'対象の型[：:]\s*`([^`]+)`', line)
            if target and target.group(1) not in expected['targets']:
                errors.append('集約にない対象の型：' + target.group(1))
    return errors
