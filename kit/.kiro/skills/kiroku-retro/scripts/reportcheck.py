"""レポートの数値・対象の型を記録と照合する。文章の意味は判定しない。"""
import json
import re

import diagnose
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
    for row in (data.get('approvals') or {}).get('groups') or []:   # 版8から。以前の版の検証値（as_version）では除く
        if row['candidate']:
            targets[row['target']] = {'count': row['approved'], 'sessions': row['sessions']}
    plan = data['reading_plan']
    found = {'since': data['since'], 'until': data['until'], 'events': data['event_count'],
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
    if data.get('diagnostics') is not None:   # 版4から。以前の版の検証値（as_version）では除く
        found['diagnostics'] = diagnose.keys(data['diagnostics'])
    return found


def extract(text):
    blocks = re.findall(r'^```kiroku-facts\s*\n(.*?)^```\s*$', text, re.M | re.S)
    if len(blocks) != 1:
        raise ValueError('kiroku-facts の JSON ブロックを1個書いてください')
    value = json.loads(blocks[0])
    if not isinstance(value, dict) or not all(k in value for k in ('since', 'until')):
        raise ValueError('kiroku-facts の期間がありません')
    return value


LENSES = tuple("V%d" % n for n in range(1, 9))   # 一歩先の観点（references/harness-guide.md）
ONE_STEP_HEADING = "## 一歩先の改善案"
FIELD = re.compile(r"^\s*[-*]\s*(観点|段|手がかり|確かめ方)[：:]\s*(.*)$")
FILE_CITATION = re.compile(r"引用（ファイル）[：:]\s*`([^`]+)`")


def one_step(text, expected, limit, paths=None, targets=()):
    """一歩先の改善案の節を確かめる。文章の意味と、案の価値は判定しない。

    確かめるのは、件数が上限以内か、各案に観点（V1〜V8）、段（観測の段→案の段。上がっていること）、
    手がかり（集約にある型か診断値を1つ以上）、確かめ方が書かれているか。
    paths は、比べたハーネスの表のファイル（版6から。それより前の版は None）。手がかりに書けるが、
    そのときは、その案の中に、そのファイルからの引用（引用（ファイル）：…）が要る。
    文書に書かれた手作業の確認などを、根拠を示して手がかりにするため。引用の実在は citations が確かめる。
    targets は、骨組みを作った時点のベストプラクティス集の候補と未確認の項目（practice:<ID>。版7から）。
    """
    lines = text.splitlines()
    if ONE_STEP_HEADING not in lines:
        return ['「%s」の節がありません。骨組みのとおりに残してください。' % ONE_STEP_HEADING[3:]]
    section = []
    for line in lines[lines.index(ONE_STEP_HEADING) + 1:]:
        if line.startswith('## '):
            break
        section.append(line)
    content = [line.strip() for line in section if line.strip()]
    if content in ([], ['なし'], ['なし。']):
        return []
    proposals, current = [], None
    for line in section:
        heading = HEADING.match(line)
        if heading:
            current = {'number': heading.group(1), 'fields': {}, 'files': set()}
            proposals.append(current)
        elif current is not None:
            field = FIELD.match(line)
            if field:
                current['fields'][field.group(1)] = field.group(2).strip()
            quoted = FILE_CITATION.search(line)
            if quoted:
                current['files'].add(quoted.group(1).strip())
    if not proposals:
        return ['一歩先の改善案は、案ごとに `### P<番号>. ` の見出しで書いてください。無ければ「なし」と書きます。']
    errors = []
    if limit is not None and len(proposals) > limit:
        errors.append('一歩先の改善案が %d 件あります。上限は %d 件です。価値の大きいものに絞り、残りは個別の改善案か'
                      '様子見にしてください。' % (len(proposals), limit))
    files = set(paths or ())
    known = set(expected.get('targets', {})) | set(expected.get('diagnostics', [])) | files | set(targets)
    for row in proposals:
        fields, name = row['fields'], row['number']
        lenses = re.findall(r'V\d+', fields.get('観点', ''))
        if not lenses or any(lens not in LENSES for lens in lenses):
            errors.append('%s：観点を V1〜V8 で書いてください（手引きの「一歩先の案」）。' % name)
        steps = [int(n) for n in re.findall(r'[1-3]', fields.get('段', ''))]
        if len(steps) < 2 or steps[1] <= steps[0]:
            errors.append('%s：段を「観測の段→案の段」で書き、案の段を観測より上にしてください'
                          '（1 事例、2 種類、3 環境）。上がらない案は、個別の改善案にします。' % name)
        cited = re.findall(r'`([^`]+)`', fields.get('手がかり', ''))
        if not any(c in known for c in cited):
            errors.append('%s：手がかりに、集約にある型か診断値（`D1:…` など）%sを1つ以上、バッククォートで'
                          '囲んで書いてください。観測につながらない一般論は、一歩先の案にしません。' % (
                              name, '、比べたハーネスの表のファイル' if paths is not None else ''))
        unquoted = sorted(c for c in set(cited) & files if c not in row['files'])
        if unquoted:
            errors.append('%s：手がかりのファイル %s からの引用（引用（ファイル）：`…`「…」）を、この案に書いてください。'
                          'ファイルを手がかりにするときは、根拠にした一節を示します。' % (
                              name, '、'.join('`%s`' % c for c in unquoted)))
        unknown = [c for c in cited if (c.startswith('D') and ':' in c or re.fullmatch(r'D\d', c)
                                        or c.split(':', 1)[0] in ('topic', 'tool', 'denied', 'cancelled',
                                                                  'timeout', 'test', 'practice', 'approval'))
                   and c not in known]
        if unknown:
            errors.append('%s：集約に無い手がかり：%s' % (name, '、'.join(unknown)))
        if not fields.get('確かめ方'):
            errors.append('%s：確かめ方（案が働いたと分かるもの）を書いてください。' % name)
    return errors


TRACKED = ('approval:', 'practice:')   # 回数や状態で追う型。手がかりに書いたら、対象の型にも書く


def untracked(text, known):
    """`approval:…`・`practice:…` を書いたのに、「対象の型」の行が無い案（版9から）。

    対象の型が無いと、台帳が採否と、その後の承認の数や状態を追えない（2026-11-25 の実機の確認の D33）。
    """
    errors, current = [], None
    sections = []
    for line in text.splitlines():
        heading = HEADING.match(line)
        if heading:
            current = [heading.group(1), [], False]
            sections.append(current)
        elif line.startswith('#'):
            current = None
        elif current is not None:
            current[1] += [c for c in re.findall(r'`([^`]+)`', line) if c.startswith(TRACKED) and c in known]
            current[2] = current[2] or bool(TARGET.search(line))
    for number, names, has_target in sections:
        if names and not has_target:
            errors.append('%s：%s を書いた案には、「- 対象の型：`%s`」の行も書いてください。採否と、その後の追跡に使います。'
                          % (number, '、'.join('`%s`' % n for n in dict.fromkeys(names)), names[0]))
    return errors


def validate(text, expected, numbers=True, extra=()):
    """numbers は、案の番号の重複を不一致にするか。版2までのレポートは、そのころの規則どおり見ない
    （台帳は、どの版のレポートでも、重複した番号への判断を記録しない）。
    extra は、検証値の外で対象の型に認めるもの（骨組みを作った時点のベストプラクティス集の候補と未確認の項目。
    版7から）。"""
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
            name = target.group(1).strip() if target else None
            if name and name.startswith('practice:') and name not in expected['targets'] and name not in extra:
                errors.append('ベストプラクティス集の候補にない対象の型：%s（対象の型に書けるのは、骨組みの'
                              '「ベストプラクティス集との照らし合わせ」の「候補」と「未確認」の項目だけです）' % name)
            elif name and name not in expected['targets'] and name not in extra:
                errors.append('集約にない対象の型：' + name)
    for number, names in titles.items() if numbers else ():
        if len(names) > 1:
            # 同じ番号の案が複数あると、「P1 は採用」がどの案のことか決まらない。
            errors.append('案の番号 %s が %d 回使われています。プロジェクト内とユーザー共通を通して、'
                          'P1 からの通し番号にしてください：%s' % (number, len(names), '／'.join(names)))
    return errors
