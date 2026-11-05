"""Export installed versions for platform-specific advisory checks; no import of apps."""
from importlib.metadata import distributions
from pathlib import Path


if __name__ == '__main__':
    output = Path('tmp/audit-requirements.txt')
    output.parent.mkdir(exist_ok=True)
    rows = sorted({f"{d.metadata['Name']}=={d.version}" for d in distributions()
                   if d.metadata['Name'].lower().replace('_', '-') != 'paper-research-assistant'})
    output.write_text('\n'.join(rows) + '\n', encoding='utf-8')
    print(f'Wrote {len(rows)} installed package versions to {output}')
