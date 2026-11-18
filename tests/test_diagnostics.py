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

    def test_missing_record_hooks_are_named(self):
        """Issue #12 改善：実行前のフックだけを見ていたので、JUnit を取り込むフックが無くても一致と出た。"""
        self.write('.kiro/kiroku/config.json', json.dumps({'tests': [
            {'match': r'run_tests\.py', 'report': 'reports/junit.xml'}]}))
        hooks = json.loads((Path(KIT).parents[2] / 'hooks/kiroku.json').read_text(encoding='utf-8'))
        doctor = __import__('doctor')
        self.assertEqual([(h['name'], h['trigger']) for h in hooks['hooks']],
                         [(name, trigger) for name, trigger, _ in doctor.RECORD_HOOKS])
        only_start = dict(hooks, hooks=[h for h in hooks['hooks'] if h['trigger'] == 'PreToolUse'])
        self.write('.kiro/hooks/kiroku.json', json.dumps(only_start))
        done = subprocess.run([sys.executable, str(Path(KIT) / 'doctor.py'), '--root', self.root,
                               '--command', 'python3 run_tests.py'], capture_output=True, text=True)
        self.assertEqual(done.returncode, 1, done.stdout)
        for name in ('kiroku-record-tool（PostToolUse）', 'kiroku-record-file', 'kiroku-record-prompt',
                     'kiroku-record-turn-end'):
            self.assertIn(name, done.stdout)
        self.assertIn('JUnit の取り込み', done.stdout)
        wrong = json.loads(json.dumps(hooks))
        wrong['hooks'][1]['trigger'] = 'PreToolUse'
        self.write('.kiro/hooks/kiroku.json', json.dumps(wrong))
        done = subprocess.run([sys.executable, str(Path(KIT) / 'doctor.py'), '--root', self.root,
                               '--command', 'python3 run_tests.py'], capture_output=True, text=True)
        self.assertIn('トリガーが PreToolUse です（PostToolUse が必要）', done.stdout)

    def test_required_agent_is_not_silently_assumed(self):
        done = subprocess.run([sys.executable, str(Path(KIT) / 'aggregate.py'),
                               '--root', self.root, '--require-agent'],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 1)
        self.assertIn('kiro-cli --agent kiroku-retro', done.stdout)
        self.assertEqual(self.events(), [])

    def test_running_agent_is_found_even_when_sessions_dir_points_to_exports(self):
        """Issue #12 指摘9：エクスポート先を設定すると、実行中の専用エージェントまで過去ログの中で探していた。"""
        import os
        import shutil
        import tempfile
        home = Path(tempfile.mkdtemp(prefix='kiroku-home-'))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        running = home / 'sessions' / 'ws' / 'sess_retro'
        running.mkdir(parents=True)
        (running / 'session.json').write_text(json.dumps({'agentMode': 'kiroku-retro'}), encoding='utf-8')
        (running / 'messages.jsonl').write_text('', encoding='utf-8')
        exported = Path(self.root) / 'exports' / 'sess_dev'
        exported.mkdir(parents=True)
        (exported / 'session.json').write_text(json.dumps({'workspacePaths': [self.root]}), encoding='utf-8')
        (exported / 'messages.jsonl').write_text('', encoding='utf-8')
        env = dict(os.environ, KIRO_HOME=str(home), KIRO_SESSION_ID='sess_retro')

        def require():
            return subprocess.run([sys.executable, str(Path(KIT) / 'aggregate.py'), '--root', self.root,
                                   '--require-agent', '--section', 'plan'],
                                  capture_output=True, text=True, env=env, cwd=self.root)
        self.assertEqual(require().returncode, 0)
        self.write('.kiro/kiroku/config.json', json.dumps({'tests': [], 'sessions_dir': 'exports'}))
        done = require()
        self.assertEqual(done.returncode, 0, done.stdout)
        # 確かめられないときは、これまでどおり止め、どの確認に失敗したかを出す。
        env['KIRO_SESSION_ID'] = 'sess_unknown'
        done = require()
        self.assertEqual(done.returncode, 1)
        self.assertIn('ログが、sessions_dir にも Kiro の既定の置き場にもありません', done.stdout)
        del env['KIRO_SESSION_ID']
        self.assertIn('KIRO_SESSION_ID がありません', require().stdout)

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
        # 見出しの形が少し違っても、台帳が案として受け取るものは検査する。
        bare = '### P1\n- 対象の型：`tool:printf NEVER_EXECUTED`\n\n```kiroku-facts\n%s\n```\n'
        unknown = check(bare % json.dumps(facts))
        self.assertEqual(unknown.returncode, 1)
        self.assertIn('集約にない対象の型', unknown.stdout)
        facts['events'] = 999
        self.assertEqual(check(body % json.dumps(facts)).returncode, 1)
        facts = data['report_facts']
        facts['events'] = 1
        self.assertEqual(check((body % json.dumps(facts)).replace('tool:make test', 'tool:unknown')).returncode, 1)
