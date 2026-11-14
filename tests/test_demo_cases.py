"""家計簿そのものを使う連携試験と、答えを明示した模擬会話の検査。

Kiro の実行や LLM の意味判断は含まない。実機結果は verification.md に分けて残す。
"""
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys

from test_kiroku import Sandbox, aggregate
from test_portability import installer
from test_review import ReviewSandbox

REPO = Path(__file__).resolve().parents[1]
CONVERSATIONS = json.loads((Path(__file__).parent / 'fixtures/demo-conversations.json').read_text(encoding='utf-8'))


class DemoProjectTest(Sandbox):
    def setUp(self):
        super().setUp()
        shutil.copytree(REPO / 'examples/demo', self.root, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        installer.install(self.root)
        self.commit('家計簿デモの初期状態')
        self.runner = self.command(sys.executable, '-B', 'run_tests.py')

    @staticmethod
    def command(*args):
        return subprocess.list2cmdline(args) if os.name == 'nt' else shlex.join(args)

    def execute(self, command):
        payload = {'tool_name': 'execute_bash', 'tool_input': {'command': command, 'cwd': self.root}}
        before = self.hook(dict(payload, hook_event_name='PreToolUse'))
        self.assertEqual((before.returncode, before.stdout, before.stderr), (0, '', ''))
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        done = subprocess.run(command, shell=True, cwd=self.root, env=env,
                              capture_output=True, text=True)
        after = self.hook(dict(payload, hook_event_name='PostToolUse',
                               tool_response='Output:\n' + done.stdout + done.stderr +
                               '\nExit Code: ' + str(done.returncode)))
        self.assertEqual((after.returncode, after.stdout, after.stderr), (0, '', ''))
        return done

    def test_full_demo_results_mentions_regression_red_and_restoration(self):
        self.assertEqual(self.execute(self.runner).returncode, 0)
        self.assertEqual(len(self.events()[-1]['results']['passed']), 11)
        pipe = self.command(sys.executable, '-B', '-c', 'import sys; sys.stdout.write(sys.stdin.read())')
        self.assertEqual(self.execute('cd . && ' + self.runner + ' 2>&1 | ' + pipe).returncode, 0)
        search = self.command(sys.executable, '-B', '-c',
                              'from pathlib import Path; print([s for s in Path("README.md").read_text(encoding="utf-8").splitlines() if "run_tests.py" in s])')
        for command in ('echo run_tests.py', search):
            self.assertEqual(self.execute(command).returncode, 0)
            self.assertEqual(self.events()[-1]['note'], 'report_stale')

        source = Path(self.root) / 'kakeibo/report.py'
        original = source.read_text(encoding='utf-8')
        self.write('kakeibo/report.py', original.replace('+= entry.amount', '+= entry.amount + 1'))
        self.saved('kakeibo/report.py')
        self.assertEqual(self.execute(self.runner).returncode, 1)
        self.assertEqual(len(self.events()[-1]['results']['failed']), 3)
        self.write('kakeibo/report.py', original)
        self.saved('kakeibo/report.py')
        self.assertEqual(self.execute(self.runner).returncode, 0)

        probe = ('import unittest\nclass ProbeTest(unittest.TestCase):\n'
                 '    def test_new(self):\n        self.assertEqual(1, 2)\n')
        self.write('tests/test_probe.py', probe)
        self.assertEqual(self.execute(self.runner).returncode, 1)
        self.write('tests/test_probe.py', probe.replace('1, 2', '1, 1'))
        self.assertEqual(self.execute(self.runner).returncode, 0)

        data = aggregate.aggregate(self.root, by='all')
        self.assertEqual(sum(p['test_runs'] for p in data['periods'].values()), 6)
        self.assertEqual(len(data['test_result_gaps']), 2)
        self.assertEqual([r['class'] for r in data['test_failures']].count('regression'), 3)
        self.assertEqual([r['class'] for r in data['test_failures']].count('red'), 1)
        self.assertEqual(len(self.events()[-1]['results']['passed']), 12)
        self.assertEqual(list(Path(self.root, '.kiro').rglob('__pycache__')), [])

    def test_demo_guard_blocks_a_dry_run_without_executing_git(self):
        done = subprocess.run([sys.executable, '-B', 'scripts/guard_shell.py'], cwd=self.root,
                              input=json.dumps({'tool_input': {'command': 'git push --force --dry-run'}}),
                              text=True, capture_output=True)
        self.assertEqual(done.returncode, 2)
        self.assertIn('止めました', done.stderr)
        self.assertEqual(done.stdout, '')

    def test_saved_versions_and_duplicate_hooks_use_the_full_demo(self):
        for text in ('A', 'B', 'A'):
            self.write('日本語の作業メモ.txt', text)
            self.saved('日本語の作業メモ.txt')
            self.hook({'hook_event_name': 'PostToolUse', 'tool_name': 'fs_write',
                       'tool_input': {'path': '日本語の作業メモ.txt'}})
        data = aggregate.aggregate(self.root, by='all')
        self.assertEqual(sum(p['writes'] for p in data['periods'].values()), 3)
        self.assertEqual(len(self.events()), 6)


class DemoConversationTest(ReviewSandbox):
    def setUp(self):
        super().setUp()
        shutil.copytree(REPO / 'examples/demo/.kiro', Path(self.root) / '.kiro', dirs_exist_ok=True)
        installer.install(self.root)
        for session in CONVERSATIONS['sessions']:
            self.log(session['id'], session['texts'])
            self.note(session['id'], session['findings'])

    def test_declared_findings_have_the_expected_candidates(self):
        data = aggregate.aggregate(self.root)
        candidates = {r['topic']: r['sessions'] for r in data['topics'] if r['candidate']}
        self.assertEqual(candidates, CONVERSATIONS['expected_candidates'])
        self.assertEqual(data['note_problems'], [])
        self.assertFalse(next(r for r in data['topics'] if r['topic'] == '前のやり方')['candidate'])
        once = next(r for r in data['topics'] if r['topic'] == '根拠の表示')
        self.assertEqual((once['count'], once['sessions'], once['candidate']), (2, 1, False))

    def test_missing_evidence_does_not_turn_one_session_into_two(self):
        shutil.rmtree(self.home / 'sessions/0123456789abcdef/demo_b')
        data = aggregate.aggregate(self.root)
        topic = next(r for r in data['topics'] if r['topic'] == '報告の順序')
        self.assertFalse(topic['candidate'])
        self.assertTrue(any('ログが無く' in r['problem'] for r in data['note_problems']))

    def test_a_fabricated_second_quote_is_not_an_extra_session(self):
        bad = dict(CONVERSATIONS['sessions'][1]['findings'][0], quote='必ずMakefileを新設してください')
        self.note('demo_b', [bad])
        data = aggregate.aggregate(self.root)
        self.assertFalse(next(r for r in data['topics'] if r['topic'] == '報告の順序')['candidate'])
        self.assertEqual(len(data['note_problems']), 1)

    def test_unchanged_evidence_has_no_unread_sessions_on_the_second_pass(self):
        first = aggregate.aggregate(self.root)
        second = aggregate.aggregate(self.root)
        self.assertEqual(first['report_facts']['unread'], 0)
        self.assertEqual(second['report_facts']['unread'], 0)
        self.assertEqual(first['topics'], second['topics'])

    def test_empty_latest_report_does_not_revive_a_previous_proposal(self):
        facts = aggregate.aggregate(self.root)['report_facts']
        body = ('### P1. 報告の順序\n- 対象の型：`topic:報告の順序`\n\n'
                '```kiroku-facts\n' + json.dumps(facts, ensure_ascii=False) + '\n```\n')
        self.report('2026-11-14-1000', body)
        self.assertEqual(len(aggregate.aggregate(self.root)['pending']), 1)
        self.report('2026-11-14-1100', '# 振り返り\n改善案なし\n')
        self.assertEqual(aggregate.aggregate(self.root)['pending'], [])
        self.assertIsNone(aggregate.ledger.resolve(self.root, 'P1'))
        self.assertIsNotNone(aggregate.ledger.resolve(self.root, '2026-11-14-1000#P1'))
