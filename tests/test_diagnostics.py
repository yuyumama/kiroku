"""導入診断とレポートの根拠を、CLI の出力で確認する。"""
import json
import subprocess
import sys
from pathlib import Path

from test_kiroku import KIT, Sandbox, aggregate


class SetupTest(Sandbox):
    def test_declared_runner_matches_but_direct_unittest_is_flagged(self):
        self.write('.kiro/kiroku/config.json', json.dumps({'tests': [
            {'match': r'run_tests\.py', 'report': 'reports/junit.xml'}]}))
        hooks = Path(KIT).parents[2] / 'hooks/kiroku.json'
        self.write('.kiro/hooks/kiroku.json', hooks.read_text(encoding='utf-8'))
        def check(command):
            return subprocess.run([sys.executable, str(Path(KIT) / 'doctor.py'),
                                   '--root', self.root, '--command', command, '--json'],
                                  capture_output=True, text=True)
        configured = check('python3 run_tests.py')
        self.assertEqual(configured.returncode, 0, configured.stderr)
        data = json.loads(configured.stdout)
        self.assertEqual(data['matched']['report'], 'reports/junit.xml')
        piped = check('python3 run_tests.py 2>&1 | tail -20')
        self.assertEqual(piped.returncode, 0, piped.stdout)
        self.assertEqual(json.loads(piped.stdout)['matched_by'], 'text')
        mismatch = check('python3 -m unittest discover tests/')
        self.assertEqual(mismatch.returncode, 1)
        self.assertIn('検出設定と一致しません', mismatch.stdout)

    def test_required_agent_is_not_silently_assumed(self):
        done = subprocess.run([sys.executable, str(Path(KIT) / 'aggregate.py'),
                               '--root', self.root, '--require-agent'],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 1)
        self.assertIn('kiro-cli --agent kiroku-retro', done.stdout)
        self.assertEqual(self.events(), [])

    def test_report_facts_and_targets_are_checked_against_records(self):
        self.shell('make test', 2)
        data = aggregate.aggregate(self.root)
        facts = data['report_facts']
        path = Path(self.root) / '.kiro/kiroku/data/reports/2026-11-14-0200.md'
        path.parent.mkdir(parents=True)
        def check(body):
            path.write_text(body, encoding='utf-8')
            return subprocess.run([sys.executable, str(Path(KIT) / 'aggregate.py'),
                                   '--root', self.root, '--check-report', str(path)],
                                  capture_output=True, text=True)
        body = '### P1. 手順\n- 対象の型：`tool:make test`\n\n```kiroku-facts\n%s\n```\n'
        self.assertEqual(check(body % json.dumps(facts)).returncode, 0)
        facts['events'] = 999
        self.assertEqual(check(body % json.dumps(facts)).returncode, 1)
        facts = data['report_facts']
        facts['events'] = 1
        self.assertEqual(check((body % json.dumps(facts)).replace('tool:make test', 'tool:unknown')).returncode, 1)
