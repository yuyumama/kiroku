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
        self.assertEqual(self.starts(), {})
        data = aggregate.aggregate(self.root, by='all')
        self.assertEqual(data['periods']['全期間']['test_runs'], 0)
        self.assertEqual(len(data['test_result_gaps']), 1)
        self.assertIn('バックグラウンドで起動', aggregate.render(data))
        # あとで生成されたレポートは、次の通常の実行の「実行前の状態」になるだけで、取り込まれない。
        self.write(REPORT, junit([('T', 'background', 'failed')]))
        self.assertEqual(self.run_ok('foreground')['results']['passed'], ['T::foreground'])

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

    def test_unexpected_log_shapes_are_not_counted(self):
        odd = self.call('c1', BLOCKED, 'denied', '{}')
        odd[1]['content'] = {}                       # 文字列でない本文
        orphan = self.call('c2', BLOCKED, 'denied', '{}')[:1]   # 結果の無い呼び出し
        other = self.call('c3', BLOCKED, 'rejected', '{}')      # 知らない状態
        self.session_log('sess_odd', [{'type': 'user', 'content': '実行'}] + odd + orphan + other)
        self.assertEqual(aggregate.aggregate(self.root)['unrecorded_calls'], [])
        self.assertTrue(os.path.isdir(self.root))
