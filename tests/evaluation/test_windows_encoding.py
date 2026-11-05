"""Chinese evaluation JSON remains readable under legacy Windows defaults."""
import json
from pathlib import Path
import sys

import pytest

from paper_assistant.material_preparation import prepare_materials
from paper_assistant.evaluation_review import main
from tests.evaluation.test_material_preparation import UnavailableMetadata


@pytest.fixture
def legacy_default(monkeypatch):
    original = Path.read_text

    def read(path, *args, **kwargs):
        if not args:
            kwargs.setdefault('encoding', 'gbk')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(data, ensure_ascii=False).encode('utf-8'))


def test_material_preparation_reads_chinese_sources_as_utf8(tmp_path, legacy_default):
    write(tmp_path / 'eval_data/agent_v1/sources.json', {'sources': {
        'test': {'arxiv_id': '2210.03629', 'version': 1, 'title': '中文论文'}}, 'fixed_seed': {}})
    report = prepare_materials(tmp_path, tmp_path / 'attempt', metadata_transport=UnavailableMetadata())
    assert report['blockers'] == ['metadata:http_503']


@pytest.mark.parametrize('command', ['review', 'compare'])
def test_review_commands_read_utf8_reports_and_reviews(tmp_path, legacy_default, monkeypatch, command):
    write(tmp_path / 'candidate/report.json', {'cases': [], 'note': '中文结果'})
    write(tmp_path / 'baseline/report.json', {'cases': [], 'note': '中文基线'})
    write(tmp_path / 'suite.json', {'cases': [], 'note': '中文题目'})
    write(tmp_path / 'reviews.json', {})
    monkeypatch.setattr(sys, 'argv', ['evaluation_review', command,
        '--candidate', str(tmp_path / 'candidate'), '--baseline', str(tmp_path / 'baseline'),
        '--suite', str(tmp_path / 'suite.json'), '--reviews', str(tmp_path / 'reviews.json'),
        '--out', str(tmp_path / 'out')])
    main()
    assert (tmp_path / 'out' / ('report.json' if command == 'review' else 'comparison.json')).exists()
