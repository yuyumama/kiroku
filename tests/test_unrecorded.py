"""実行後のフックが呼ばれない場合（フックによる停止、中断、バックグラウンド実行）の扱い。

ログの形は、Kiro CLI 3.3.0 で実測したもの（docs/retro/verification.md の 2026-11-15）に合わせる。
フック入力とログは模擬で、Kiro の実発火を証明するものではない。
"""
import datetime
import json
import os
from pathlib import Path
import time

from test_kiroku import KIT, REPORT, aggregate, junit
from test_review import NOW, ReviewSandbox

BLOCKED = 'git push --force --dry-run'


class LeftoverStartTest(ReviewSandbox):
    def pre(self, command, **tool_input):
        return self.hook({'hook_event_name': 'PreToolUse', 'tool_name': 'execute_bash',
                          'tool_input': dict({'command': command}, **tool_input)})

    def starts(self):
        try:
            with open(self.data / 'state.json', encoding='utf-8') as handle:
                return json.load(handle).get('test_starts', {})
        except OSError:
            return {}

    def run_ok(self, name):
        self.pre('pytest')
        time.sleep(0.02)
        self.write(REPORT, junit([('T', name, 'passed')]))
        self.shell('pytest', 0)
        return self.events()[-1]

    def test_stop_closes_a_start_left_by_a_blocked_test_command(self):
        """別のフックが止めたテスト実行は PostToolUse が来ない。Stop で閉じ、次の結果を取り込む。"""
        self.pre(BLOCKED + ' ; pytest')
        self.assertEqual(len(self.starts()), 1)
        done = self.hook({'hook_event_name': 'Stop'})
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, '', ''))
        self.assertEqual(self.starts(), {})
        gap = self.events()[-1]
        self.assertEqual((gap['type'], gap['reason'], gap['tool'], gap['session_id']),
                         ('recording_gap', 'test_end_missing', 'execute_bash', self.session))
        read = self.run_ok('after_stop')
        self.assertEqual(read['results']['passed'], ['T::after_stop'])
        self.assertNotIn('note', read)
        text = aggregate.render(aggregate.aggregate(self.root))
        self.assertIn('テスト実行の終わりが記録されなかった', text)

    def test_next_prompt_closes_a_start_when_stop_never_fires(self):
        """取り消したターンでは Stop が呼ばれない。同じセッションの次の発話で閉じる。"""
        self.pre('pytest')   # 実行中に中断。PostToolUse も Stop も来ない
        self.hook({'hook_event_name': 'UserPromptSubmit', 'prompt': '続けて'})
        self.assertEqual(self.starts(), {})
        self.assertEqual([e['type'] for e in self.events()], ['recording_gap', 'prompt'])
        self.assertEqual(self.run_ok('after_prompt')['results']['passed'], ['T::after_prompt'])

    def test_only_the_same_session_is_closed(self):
        self.pre('pytest')
        self.session = 'sess_other'
        self.hook({'hook_event_name': 'Stop'})
        self.hook({'hook_event_name': 'UserPromptSubmit', 'prompt': '別のセッション'})
        self.assertEqual(len(self.starts()), 1)
        self.assertEqual([e['type'] for e in self.events()], ['prompt'])

    def test_stop_without_leftovers_records_nothing(self):
        self.run_ok('normal')
        before = len(self.events())
        self.hook({'hook_event_name': 'Stop'})
        self.assertEqual(len(self.events()), before)

    def test_late_post_after_closing_is_held_not_misread(self):
        """閉じたあとに遅れて実行後のフックが来ても、古い結果を取り込まない。"""
        self.write(REPORT, junit([('Old', 'old', 'failed')]))
        self.pre('pytest')
        self.hook({'hook_event_name': 'Stop'})
        self.shell('pytest', 0)
        event = self.events()[-1]
        self.assertEqual((event['results'], event['note']), (None, 'missing_test_start'))

    def test_background_launch_is_not_recorded_as_a_finished_run(self):
        """バックグラウンドの実行では、処理が終わる前に終了コード 0 で実行後のフックが呼ばれる。"""
        self.pre('pytest', run_in_background=True)
        self.hook({'hook_event_name': 'PostToolUse', 'tool_name': 'execute_bash',
                   'tool_input': {'command': 'pytest', 'run_in_background': True},
                   'tool_response': 'Output:\n\n\nExit Code: 0'})
        event = self.events()[-1]
        self.assertEqual((event['type'], event['results'], event['note'], event['background']),
                         ('test_run', None, 'background_not_finished', True))
        self.assertEqual([s.get('background') for s in self.starts().values()], [True])
        self.hook({'hook_event_name': 'Stop'})   # ターンが終わっても、裏の処理は続いている
        self.assertEqual(len(self.starts()), 1)
        data = aggregate.aggregate(self.root, by='all')
        self.assertEqual(data['periods']['全期間']['test_runs'], 0)
        self.assertEqual(len(data['test_result_gaps']), 1)
        self.assertEqual(data['recording_gaps'], [])
        self.assertIn('バックグラウンドで起動', aggregate.render(data))
        # 裏の処理がレポートを書き終えたあとの通常の実行は、取り込む。
        time.sleep(0.02)
        self.write(REPORT, junit([('T', 'background', 'failed')]))
        self.assertEqual(self.run_ok('foreground')['results']['passed'], ['T::foreground'])
        self.assertEqual(self.starts(), {})

    def test_report_written_by_a_background_run_is_not_given_to_a_later_run(self):
        """裏の処理がまだレポートを書いていない間に始まった通常の実行には、結果を帰属させない。"""
        self.pre('pytest', run_in_background=True)
        self.hook({'hook_event_name': 'PostToolUse', 'tool_name': 'execute_bash',
                   'tool_input': {'command': 'pytest', 'run_in_background': True},
                   'tool_response': 'Output:\n\n\nExit Code: 0'})
        self.pre('pytest -x')
        time.sleep(0.02)
        self.write(REPORT, junit([('Review', 'background-result', 'passed')]))   # 裏の処理が書いた
        self.shell('pytest -x', 2, 'runner did not start')
        event = self.events()[-1]
        self.assertEqual((event['results'], event['note']), (None, 'ambiguous_test_run'))

    def test_background_mark_survives_reports_written_by_other_runs(self):
        """実機で再現。裏の処理が動いている間の通常の実行が書いたレポートを、裏の処理の完了とみなさない。"""
        def background():
            return [s for s in self.starts().values() if s.get('background')]

        self.pre('pytest', run_in_background=True)
        self.hook({'hook_event_name': 'PostToolUse', 'tool_name': 'execute_bash',
                   'tool_input': {'command': 'pytest', 'run_in_background': True},
                   'tool_response': 'Output:\n\n\nExit Code: 0'})
        for name in ('first', 'second'):   # 裏の処理はまだ書いていない
            held = self.run_ok(name)
            self.assertEqual((held['results'], held['note']), (None, 'ambiguous_test_run'), name)
            self.assertEqual(len(background()), 1, name)
        time.sleep(0.02)
        self.write(REPORT, junit([('T', 'background', 'passed')]))   # 裏の処理が書き終えた
        self.assertEqual(self.run_ok('third')['results']['passed'], ['T::third'])
        self.assertEqual(background(), [])

    def test_runs_that_may_write_the_same_report_file_are_ambiguous(self):
        """レポートの指定が違っても、同じファイルを指しうるなら、結果の帰属を確定しない。"""
        self.write('.kiro/kiroku/config.json', json.dumps({'tests': [
            {'match': 'pytest a', 'report': 'reports/*.xml'},
            {'match': 'pytest b', 'report': 'reports/junit.xml'}]}))
        self.pre('pytest a')
        self.session = 'sess_other'
        self.pre('pytest b')
        self.write(REPORT, junit([('Review', 'a-result', 'passed')]))
        self.shell('pytest b', 1)
        event = [e for e in self.events() if e['type'] == 'test_run'][-1]
        self.assertEqual((event['results'], event['note']), (None, 'ambiguous_test_run'))
        import record
        self.assertFalse(record.may_share_report('reports/junit.xml', 'build/junit.xml'))
        self.assertTrue(record.may_share_report('build/**/*.xml', 'build/test-results/a.xml'))
        self.assertTrue(record.may_share_report('reports/*.xml', 'reports/unit/*.xml'))
        self.assertFalse(record.may_share_report('reports/*.xml', 'build/*.xml'))

    def test_changing_only_file_permissions_does_not_make_an_old_report_new(self):
        self.write(REPORT, junit([('Review', 'old-run', 'passed')]))
        path = os.path.join(self.root, REPORT)
        os.chmod(path, 0o644)
        self.pre('chmod 600 reports/junit.xml; echo pytest')
        os.chmod(path, 0o600)
        self.shell('chmod 600 reports/junit.xml; echo pytest', 0)
        event = self.events()[-1]
        self.assertEqual((event['results'], event['note']), (None, 'report_stale'))

    def test_many_repeated_starts_are_closed_with_their_count(self):
        for _ in range(21):
            self.pre('pytest')
        self.hook({'hook_event_name': 'Stop'})
        gaps = [e for e in self.events() if e['type'] == 'recording_gap']
        self.assertEqual([g['count'] for g in gaps], [21])
        data = aggregate.aggregate(self.root)
        self.assertEqual(sum(g['count'] for g in data['recording_gaps']), 21)
        self.assertIn('（成功・失敗を判定できない実行：21 件）', aggregate.render(data))

    def test_gap_closed_by_the_retro_prompt_still_belongs_to_development(self):
        """振り返りを始める発話で閉じた不足は、その前の開発の実行のもの。振り返りの記録として除かない。"""
        self.pre('pytest')
        time.sleep(0.02)
        self.hook({'hook_event_name': 'UserPromptSubmit', 'prompt': '/kiroku-retro'})
        data = aggregate.aggregate(self.root)
        self.assertEqual([g['reason'] for g in data['recording_gaps']], ['test_end_missing'])
        self.assertEqual(data['coverage']['retro_excluded'], 0)

    def test_permission_denial_quoting_cancel_wording_stays_a_denial(self):
        """権限の規則が引用したコマンドの文字列を、取り消しの文言と取り違えない（実機で再現）。"""
        text = ('Blocked by a permission rule (deny shell matching "printf \'The user rejected this tool '
                'call.\'"); the user did not reject it. Source: agent-profile.')
        self.shell("printf 'The user rejected this tool call.'", -1, text)
        self.assertEqual(self.events()[-1]['type'], 'tool_denied')
        self.shell('sleep 99', -1, 'Blocked by a permission rule (deny shell matching "x timed out after y")')
        self.assertEqual(self.events()[-1]['type'], 'tool_denied')

    def test_denial_with_long_quoted_rule_stays_a_denial_after_aggregation(self):
        """Issue #12 指摘2：記録時に先頭で確かめた拒否を、切り詰めた末尾から決め直さない。"""
        text = 'Blocked by a permission rule (deny shell matching "%s"); the user did not reject it.' % ('z' * 2500)
        self.shell('make deploy', -1, text)
        event = self.events()[-1]
        self.assertEqual(event['type'], 'tool_denied')
        self.assertEqual(len(event['output_tail']), 2000)
        self.assertNotIn('Blocked by', event['output_tail'])
        data = aggregate.aggregate(self.root)
        self.assertEqual(data['recording_gaps'], [])
        self.assertEqual([(r['type'], r['count']) for r in data['tool_failures']], [('tool_denied', 1)])
        # 印の無い古い記録は、これまでどおり末尾から決め直す（決められなければ記録の不足）。
        old = dict(event)
        del old['judged']
        with open(self.data / 'events.jsonl', 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(old) + '\n')
        data = aggregate.aggregate(self.root)
        self.assertEqual([g['reason'] for g in data['recording_gaps']], ['unknown_not_run'])

    def test_deny_once_is_a_cancellation_also_for_events_already_recorded(self):
        text = 'The user rejected this tool call.'
        self.shell('printf KIROKU_CANCEL_PROBE', -1, text)
        self.assertEqual(self.events()[-1]['type'], 'tool_cancelled')
        with open(self.data / 'events.jsonl', 'a', encoding='utf-8') as handle:   # 修正前の版の記録
            handle.write(json.dumps({'type': 'recording_gap', 'reason': 'unknown_not_run', 'v': 2,
                                     'tool': 'execute_bash', 'command': 'printf KIROKU_CANCEL_PROBE',
                                     'output_tail': text, 'session_id': self.session,
                                     'ts': NOW.isoformat().replace('+00:00', 'Z')}) + '\n')
        data = aggregate.aggregate(self.root)
        self.assertEqual(data['recording_gaps'], [])
        cancelled = [r for r in data['tool_failures'] if r['type'] == 'tool_cancelled']
        self.assertEqual([(r['command_key'], r['count']) for r in cancelled],
                         [('printf KIROKU_CANCEL_PROBE', 2)])

    def test_distributed_hooks_include_the_turn_end_hook(self):
        hooks = json.loads((Path(KIT).parents[2] / 'hooks/kiroku.json').read_text(encoding='utf-8'))['hooks']
        self.assertEqual([h['trigger'] for h in hooks],
                         ['PreToolUse', 'PostToolUse', 'PostFileSave', 'UserPromptSubmit', 'Stop'])


class UnrecordedCallTest(ReviewSandbox):
    """セッションログから、実行後の記録が無い呼び出しだけを拾う。"""

    def session_log(self, session, rows):
        directory = self.home / 'sessions' / '0123456789abcdef' / session
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'session.json').write_text(json.dumps(
            {'id': session, 'workspacePaths': [self.root]}), encoding='utf-8')
        start = NOW - datetime.timedelta(minutes=30)
        with open(directory / 'messages.jsonl', 'w', encoding='utf-8') as handle:
            for index, payload in enumerate(rows):
                stamp = (start + datetime.timedelta(seconds=index)).isoformat()
                handle.write(json.dumps({'id': str(index), 'timestamp': stamp, 'payload': payload},
                                        ensure_ascii=False) + '\n')

    @staticmethod
    def hook_row(name, turn='t1'):
        return {'type': 'ContextualHookInvoked', 'hookId': 'h', 'operationId': 'o', 'name': name,
                'hookActionType': 'runCommand', 'status': 'completed', 'executionId': 'e', 'turnId': turn}

    @staticmethod
    def call(call_id, command, status, content, turn='t1', policy=False):
        meta = {'kiro': {'agentMode': 'dev', 'toolOrigin': 'builtin'}}
        if policy:
            meta['kiro']['policyDenial'] = {'effect': 'deny', 'source': 'agent-profile'}
        return [{'type': 'tool_call', 'toolCallId': call_id, 'toolName': 'execute_bash', 'status': status,
                 'args': {'command': command}, 'executionId': 'e', 'turnId': turn, '_meta': meta},
                {'type': 'tool_result', 'toolCallId': call_id, 'content': content, 'success': False,
                 'executionId': 'e', 'turnId': turn, '_meta': {'kiro': {'agentMode': 'dev'}}}]

    @staticmethod
    def resolved(call_id, outcome, option=None):
        row = {'type': 'interaction_resolved', 'toolCallId': call_id, 'outcome': outcome,
               'executionId': 'e', 'consentRound': 1}
        if option:
            row['selectedOption'] = option
        return row

    def rows(self):
        pre = [self.hook_row('kiroku-record-test-start'), self.hook_row('guard-shell')]
        rows = [{'type': 'user', 'content': '実行してください'}, self.hook_row('kiroku-record-prompt')]
        rows += pre + self.call('c1', BLOCKED, 'denied', '{}')
        rows += pre + self.call('c2', 'printf KIROKU_DENIED_PROBE', 'denied',
                                'Blocked by a permission rule (deny shell matching "x"); the user did not '
                                'reject it. Source: agent-profile.\n\nExit Code: -1', policy=True)
        rows += pre + [self.resolved('c3', 'selected', 'reject')] + self.call(
            'c3', 'printf KIROKU_CANCEL_PROBE', 'denied', 'The user rejected this tool call.\n\nExit Code: -1')
        rows += pre + [self.resolved('c4', 'cancelled')] + self.call(
            'c4', 'printf KIROKU_ESC_PROBE', 'denied',
            'The tool call was cancelled before it was approved.\n\nExit Code: -1')
        rows += pre + self.call('c5', 'ls /no/such/kiroku-d16', 'failed',
                                "ls: cannot access '/no/such/kiroku-d16'\n\nExit Code: 2")
        rows += pre + [self.resolved('c6', 'selected', 'accept')] + self.call(
            'c6', 'sleep 30', 'failed', 'This tool was interrupted before it reported a result, '
            'so it may or may not have taken effect.')
        return rows

    def test_only_hook_stops_and_interruptions_are_picked_up(self):
        self.session_log('sess_live', self.rows())
        data = aggregate.aggregate(self.root)
        found = {(r['kind'], r['command_key']): r for r in data['unrecorded_calls']}
        self.assertEqual(sorted(found), [('blocked', BLOCKED), ('interrupted', 'sleep 30')])
        blocked = found[('blocked', BLOCKED)]
        self.assertEqual((blocked['count'], blocked['sessions'], blocked['hooks']), (1, 1, ['guard-shell']))
        self.assertEqual(found[('interrupted', 'sleep 30')]['hooks'], [])
        text = aggregate.render(data)
        self.assertIn('## 実行前に止められた呼び出し（1 種類）', text)
        self.assertIn('## 実行中に中断された呼び出し（1 種類）', text)
        self.assertIn('止めたフックとは限らない', text)
        # 追跡の対象にはしない。レポートの「対象の型」として書かれても、集約にない型として扱う。
        self.assertFalse(any(BLOCKED in key for key in data['report_facts']['targets']))

    def test_hooks_that_cannot_block_are_not_candidates(self):
        """実機で、発話のときに動く観測用フックが候補に混ざった。実行前のフックだけを候補にする。"""
        self.write('.kiro/hooks/project.json', json.dumps({'version': 'v1', 'hooks': [
            {'name': 'guard-shell', 'trigger': 'PreToolUse', 'action': {'type': 'command', 'command': 'x'}},
            {'name': 'diagnostic-UserPromptSubmit', 'trigger': 'UserPromptSubmit',
             'action': {'type': 'command', 'command': 'x'}},
            {'name': 'check-python-syntax', 'trigger': 'PostFileSave',
             'action': {'type': 'command', 'command': 'x'}}]}))
        rows = [{'type': 'user', 'content': '実行'}, self.hook_row('diagnostic-UserPromptSubmit'),
                self.hook_row('check-python-syntax'), self.hook_row('removed-hook'),
                self.hook_row('guard-shell')] + self.call('c1', BLOCKED, 'denied', '{}')
        self.session_log('sess_hooks', rows)
        row = aggregate.aggregate(self.root)['unrecorded_calls'][0]
        self.assertEqual(row['hooks'], ['removed-hook', 'guard-shell'])   # 定義の無いものは残す

    def test_repeats_across_sessions_are_grouped_and_wrappers_are_ignored(self):
        for session, command in (('sess_a', BLOCKED), ('sess_b', 'cd . && ' + BLOCKED + ' 2>&1')):
            self.session_log(session, [{'type': 'user', 'content': '実行'}] + self.call('c', command, 'denied', '{}'))
        row = aggregate.aggregate(self.root)['unrecorded_calls'][0]
        self.assertEqual((row['command_key'], row['count'], row['sessions']), (BLOCKED, 2, 2))

    def test_one_blocked_call_among_parallel_calls_in_a_turn(self):
        """同じターンに複数の呼び出しがあっても、止められた1件だけを拾う。"""
        rows = [{'type': 'user', 'content': '2つ実行'}, self.hook_row('kiroku-record-test-start'),
                self.hook_row('guard-shell')] + self.call('p1', BLOCKED, 'denied', '{}')
        rows += [self.hook_row('kiroku-record-test-start'), self.hook_row('guard-shell'),
                 {'type': 'tool_call', 'toolCallId': 'p2', 'toolName': 'execute_bash', 'status': 'completed',
                  'args': {'command': 'printf KIROKU_MULTI_C'}, 'turnId': 't1'},
                 {'type': 'tool_result', 'toolCallId': 'p2', 'content': 'Output:\nx\n\nExit Code: 0',
                  'success': True, 'turnId': 't1'}, self.hook_row('kiroku-record-tool')]
        self.session_log('sess_multi', rows)
        found = aggregate.aggregate(self.root)['unrecorded_calls']
        self.assertEqual([(r['kind'], r['command_key'], r['count']) for r in found], [('blocked', BLOCKED, 1)])

    def test_calls_after_the_retro_started_are_not_development_records(self):
        rows = [{'type': 'user', 'content': '/kiroku-retro'}] + self.call('c', BLOCKED, 'denied', '{}')
        self.session_log('sess_retro', rows)
        self.assertEqual(aggregate.aggregate(self.root)['unrecorded_calls'], [])

    def test_unexpected_log_shapes_are_shown_as_gaps_not_dropped(self):
        """判定できない形は、止められたとも決めず、黙って捨てもしない。記録の不足に出す。"""
        odd = self.call('c1', BLOCKED, 'denied', '{}')
        odd[1]['content'] = {}                       # 文字列でない本文
        orphan = self.call('c2', BLOCKED, 'denied', '{}')[:1]   # 結果の無い呼び出し
        other = self.call('c3', BLOCKED, 'rejected', '{}')      # 知らない状態
        self.session_log('sess_odd', [{'type': 'user', 'content': '実行'}] + odd + orphan + other)
        data = aggregate.aggregate(self.root)
        self.assertEqual(data['unrecorded_calls'], [])
        self.assertEqual([g['reason'] for g in data['recording_gaps']], ['unreadable_tool_call'] * 3)
        self.assertIn('形式が想定と違う', aggregate.render(data))

    def test_call_inside_the_period_is_found_even_when_its_prompt_is_outside(self):
        directory = self.home / 'sessions' / '0123456789abcdef' / 'sess_edge'
        directory.mkdir(parents=True)
        (directory / 'session.json').write_text(json.dumps(
            {'id': 'sess_edge', 'workspacePaths': [self.root]}), encoding='utf-8')
        edge = NOW - datetime.timedelta(hours=1)
        rows = [(edge - datetime.timedelta(seconds=1), {'type': 'user', 'content': '実行'})]
        rows += [(edge + datetime.timedelta(seconds=1), p) for p in self.call('c1', BLOCKED, 'denied', '{}')]
        with open(directory / 'messages.jsonl', 'w', encoding='utf-8') as handle:
            for index, (moment, payload) in enumerate(rows):
                handle.write(json.dumps({'id': str(index), 'timestamp': moment.isoformat(),
                                         'payload': payload}, ensure_ascii=False) + '\n')
        data = aggregate.aggregate(self.root, since=edge)
        self.assertEqual(data['sessions'], [])
        self.assertEqual([(r['kind'], r['count']) for r in data['unrecorded_calls']], [('blocked', 1)])

    def test_exit_code_mentioned_inside_the_text_is_not_an_exit_code_line(self):
        rows = [{'type': 'user', 'content': '実行'}] + self.call(
            'c1', 'sleep 30', 'failed', 'Diagnostic mentioned Exit Code: 0; no actual exit status')
        self.session_log('sess_text', rows)
        found = aggregate.aggregate(self.root)['unrecorded_calls']
        self.assertEqual([(r['kind'], r['command_key']) for r in found], [('interrupted', 'sleep 30')])

    def test_blocked_and_interrupted_counts_are_part_of_the_report_facts(self):
        self.session_log('sess_live', self.rows())
        facts = aggregate.aggregate(self.root)['report_facts']
        self.assertEqual(facts['unrecorded_calls'], {'blocked': 1, 'interrupted': 1})
