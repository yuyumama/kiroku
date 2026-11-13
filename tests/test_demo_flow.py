"""一時導入先で本物のランナーを実行し、記録から次回レポートまで照合する。"""
import json
from pathlib import Path
import shutil
import subprocess
import sys

from test_kiroku import Sandbox
from test_portability import installer


class DemoFlowTest(Sandbox):
    def test_real_runs_report_check_and_next_empty_report(self):
        root = Path(self.root)
        installer.install(root)
        scripts = root / '.kiro/skills/kiroku-retro/scripts'
        self.write('.kiro/kiroku/config.json', json.dumps({'tests': [
            {'match': r'run_tests\.py', 'report': 'reports/junit.xml'}]}))
        shutil.copyfile(Path(__file__).resolve().parents[1] / 'examples/demo/run_tests.py', root / 'run_tests.py')
        self.write('value.py', 'VALUE = 1\n')
        self.write('tests/__init__.py', '')
        self.write('tests/test_value.py',
                   'import unittest\nfrom value import VALUE\nclass ValueTest(unittest.TestCase):\n'
                   '    def test_value(self):\n        self.assertEqual(VALUE, 1)\n')
        self.commit('導入先の初期状態')

        def cli(script, *args):
            return subprocess.run([sys.executable, '-B', str(scripts / script), *args],
                                  cwd=root, capture_output=True, text=True)

        def hook(kind, response=None):
            payload = {'cwd': str(root), 'session_id': 'flow', 'hook_event_name': kind,
                       'tool_name': 'execute_bash', 'tool_input': {'command': 'python3 run_tests.py'}}
            if response is not None:
                payload['tool_response'] = response
            result = subprocess.run([sys.executable, '-B', str(scripts / 'record.py')], cwd=root,
                                    input=json.dumps(payload), capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))

        def run(expected):
            hook('PreToolUse')
            result = subprocess.run([sys.executable, '-B', 'run_tests.py'], cwd=root,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, expected)
            hook('PostToolUse', 'Output:\n' + result.stdout + result.stderr +
                 '\nExit Code: ' + str(result.returncode))

        self.assertEqual(cli('doctor.py', '--command', 'python3 run_tests.py').returncode, 0)
        run(0)
        self.write('value.py', 'VALUE = 2\n')
        run(1)
        self.write('value.py', 'VALUE = 1\n')
        run(0)
        with (root / 'tests/test_value.py').open('a') as handle:
            handle.write('    def test_new(self):\n        self.assertEqual(VALUE, 2)\n')
        run(1)
        data = json.loads(cli('aggregate.py', '--since', 'all', '--json').stdout)
        self.assertEqual(sum(p['test_runs'] for p in data['periods'].values()), 4)
        self.assertEqual({f['class'] for f in data['test_failures']}, {'red', 'regression'})
        self.assertEqual(data['test_result_gaps'], [])
        target = next(k for k in data['report_facts']['targets'] if k.startswith('test:'))
        report = root / '.kiro/kiroku/data/reports/2026-11-14-0200.md'
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text('### P1. 記録の確認\n- 対象の型：`%s`\n\n```kiroku-facts\n%s\n```\n'
                          % (target, json.dumps(data['report_facts'], ensure_ascii=False)), encoding='utf-8')
        self.assertEqual(cli('aggregate.py', '--check-report', str(report)).returncode, 0)
        (report.parent / '2026-11-14-0300.md').write_text('# 次回\n改善案なし\n', encoding='utf-8')
        self.assertEqual(cli('ledger.py', 'adopt', 'P1').returncode, 1)
        self.assertFalse((root / '.kiro/kiroku/data/ledger.jsonl').exists())
