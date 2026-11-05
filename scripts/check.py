"""Run engineering regression without credentials, downloads or paid calls."""
import json
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run_checks(root=ROOT):
    node = shutil.which('node')
    if not node:
        print('Node.js is required for frontend regression.', file=sys.stderr)
        return 2
    output_root = root / 'tmp' / 'checks'
    output_root.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix='run-', dir=output_root))
    env = dict(os.environ)
    for name in list(env):
        if name.lower().endswith('_proxy') or name in ('OPENAI_API_KEY', 'OPENAI_ADMIN_KEY', 'HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN'):
            env.pop(name)
    env.update(OPENAI_API_KEY='', PAPER_TEST_GUARD='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               HF_HUB_DISABLE_TELEMETRY='1', PYTHONIOENCODING='utf-8', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
               PYTHONPATH=os.pathsep.join([str(root / 'scripts/checks'), str(root / 'src'), str(root)]))
    env.pop('PYTEST_ADDOPTS', None)
    env.pop('NODE_OPTIONS', None)
    commands = [
        ('python', [sys.executable, '-m', 'pytest', '-q', '-ra', '-p', 'no:cacheprovider',
                    '--basetemp', str(output / 'pytest'), '--junitxml', str(output / 'python.xml'), 'tests']),
        ('frontend', [node, '--require', str(root / 'scripts/checks/node_offline.cjs'), '--test',
                      'tests/web/research_client.test.cjs', 'tests/web/conversation_client.test.cjs',
                      'tests/checks/node_offline.test.cjs']),
    ]
    manifest = {'kind': 'engineering_regression', 'python': platform.python_version(),
                'platform': platform.platform(), 'results': [], 'quality_evaluation': False,
                'node': subprocess.check_output([node, '--version'], env=env, text=True, timeout=10).strip(),
                'lock_sha256': hashlib.sha256((root / 'uv.lock').read_bytes()).hexdigest() if (root / 'uv.lock').exists() else None}
    print(f'Reports: {output}', flush=True)
    for name, command in commands:
        try:
            result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, encoding='utf-8', timeout=600)
            code, log = result.returncode, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            code, log = 124, f'{name}: exceeded the 600 second regression timeout\n'
        except OSError as error:
            code, log = 2, f'{name}: could not start ({type(error).__name__})\n'
        (output / f'{name}.log').write_text(log, encoding='utf-8')
        print(log, flush=True)
        manifest['results'].append({'suite': name, 'exit_code': code})
        (output / 'summary.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return int(any(row['exit_code'] for row in manifest['results']))


if __name__ == '__main__':
    raise SystemExit(run_checks())
