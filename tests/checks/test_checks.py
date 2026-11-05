import json
from pathlib import Path
import shutil

from scripts.check import run_checks
from scripts.checks.scan_secrets import scan


def test_scanner_rejects_credentials_without_printing_values_or_reading_dotenv(tmp_path):
    secret = 'sk-' + 'a' * 48
    (tmp_path / 'source.txt').write_text(secret)
    # No .env needs to exist: rejecting by name cannot leak its contents.
    findings = scan(tmp_path, ['source.txt', '.env'])
    assert len(findings) == 2
    assert secret not in str(findings)


def test_runner_keeps_failure_exit_and_runs_frontend_after_python_failure(tmp_path):
    (tmp_path / 'tests/web').mkdir(parents=True)
    (tmp_path / 'tests/checks').mkdir()
    (tmp_path / 'scripts/checks').mkdir(parents=True)
    (tmp_path / 'scripts/checks/node_offline.cjs').write_text('')
    for name in ('web/research_client.test.cjs', 'web/conversation_client.test.cjs', 'checks/node_offline.test.cjs'):
        (tmp_path / 'tests' / name).write_text("require('node:test')('fixture', () => {});")
    (tmp_path / 'tests/test_deliberate_failure.py').write_text('def test_failure():\n    assert False, "intentional temporary probe"\n')
    assert shutil.which('node'), 'Node is required for the unified regression suite'
    assert run_checks(tmp_path) == 1
    reports = list((tmp_path / 'tmp/checks').glob('run-*/summary.json'))
    summary = json.loads(reports[0].read_text())
    assert summary['results'] == [{'suite': 'python', 'exit_code': 1}, {'suite': 'frontend', 'exit_code': 0}]
