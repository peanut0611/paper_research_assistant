"""Conservative credential-pattern check of tracked source, with redacted output.

This is a basic gate, not an exhaustive entropy or history secret scanner.
Forbidden credential files are rejected by name WITHOUT opening them.
"""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
PATTERNS = (
    re.compile(rb'sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{40,}'),
    re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})'),
    re.compile(rb'AKIA[A-Z0-9]{16}'),
    re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
)


def forbidden(path):
    return ((path.name == '.env' or path.name.startswith('.env.'))
            and path.name not in ('.env.example', '.env.template')) or path.suffix.lower() in ('.pem', '.key', '.p12', '.pfx')


def scan(root, names):
    findings = []
    for name in names:
        path = root / name
        if forbidden(path) or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            findings.append((name, 0, 'forbidden credential file or symlink (not read)'))
            continue
        if not path.exists():  # A tracked file may have been deleted locally.
            continue
        data = path.read_bytes()
        for pattern in PATTERNS:
            for match in pattern.finditer(data):
                findings.append((name, data[:match.start()].count(b'\n') + 1, 'credential pattern (redacted)'))
    return findings


def main():
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode('utf-8').split('\0')
    findings = scan(ROOT, [name for name in names if name])
    for name, line, reason in findings:
        print(f'{name}:{line}: {reason}')
    print(f'Tracked-source credential check: {len(findings)} findings; ignored files and Git history not scanned.')
    return int(bool(findings))


if __name__ == '__main__':
    raise SystemExit(main())
