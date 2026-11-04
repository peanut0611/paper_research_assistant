"""Prepare evaluation materials from version-specific official API responses.

This entry point never invokes a model or assigns semantic evidence scores.
Injected external transports remain visibly synthetic, including in saved seeds.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlencode

from paper_assistant.tools.arxiv.search import (
    ARXIV_ENDPOINT, MAX_RESPONSE_BYTES, FileRequestGate, LiteratureSearchError,
    SearchRequest, UrllibTransport, parse_arxiv_feed,
)


def _save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def prepare_materials(root, out, *, metadata_transport=None, pdf_transport=None, gate=None):
    """Create a new attempt directory; never overwrite frozen or source inputs.

    Only metadata/PDF transports and the shared request gate are replaceable.
    Partial attempts retain a report, downloaded cache and raw official response.
    A successful acquisition still needs human review; it is not a quality pass.
    """
    root, out = Path(root).resolve(), Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    book = copy.deepcopy(json.loads((root / 'eval_data/agent_v1/sources.json').read_text()))
    report = {'status': 'blocked', 'blockers': [], 'sources': {},
              'model_calls': 0, 'review_status': 'not_reviewed'}
    source_origin = 'live_arxiv_api' if metadata_transport is None else 'injected_transport'
    report['metadata_origin'] = source_origin
    transport = metadata_transport if metadata_transport is not None else UrllibTransport()
    gate = gate if gate is not None else FileRequestGate(root / 'data/literature_search')
    identifiers = [f"{s['arxiv_id']}v{s['version']}" for s in book['sources'].values()]
    url = ARXIV_ENDPOINT + '?' + urlencode({'id_list': ','.join(identifiers), 'start': 0,
                                           'max_results': len(identifiers)})
    report['metadata_request_url'] = url
    try:
        with gate.slot(time.monotonic() + 30):
            response = transport.get(url, timeout=30, max_bytes=MAX_RESPONSE_BYTES)
        if response.status != 200:
            raise LiteratureSearchError(f'http_{response.status}', 'Official metadata request failed')
        if response.content_type.split(';')[0].strip() not in ('application/atom+xml', 'application/xml', 'text/xml'):
            raise LiteratureSearchError('invalid_content_type', 'Expected Atom metadata')
        (out / 'metadata.xml').write_bytes(response.body)
        suite = json.loads((root / 'eval_data/agent_v1/suite.json').read_text())
        starts = [datetime.fromisoformat(c['web_payload']['start_date']).replace(tzinfo=timezone.utc) for c in suite['cases']]
        ends = [datetime.fromisoformat(c['web_payload']['end_date']).replace(tzinfo=timezone.utc) for c in suite['cases']]
        request = SearchRequest('fixed arXiv versions', max(starts), min(ends), len(identifiers))
        now = datetime.now(timezone.utc)
        rows, total, duplicates = parse_arxiv_feed(response.body, request, now)
        expected = {(s['arxiv_id'], s['version']) for s in book['sources'].values()}
        if {(r.arxiv_id, r.version) for r in rows} != expected or duplicates:
            raise ValueError('pinned_versions_mismatch')
        by_id = {(r.arxiv_id, r.version): r for r in rows}
        for source in book['sources'].values():
            row = by_id[(source['arxiv_id'], source['version'])]
            if (' '.join(row.title.split()).casefold() != ' '.join(source['title'].split()).casefold()
                    or row.submitted_at != datetime.fromisoformat(source['submitted_at'])
                    or not request.start_at <= row.submitted_at < request.end_at):
                raise ValueError('frozen_metadata_mismatch')
        seed = {'status': 'success', 'snapshot_origin': source_origin,
                'request': request.to_dict(), 'request_url': url,
                'request_kind': 'exact_version_id_list', 'candidates': [r.to_dict() for r in rows],
                'raw_sha256': hashlib.sha256(response.body).hexdigest(),
                'raw_response_path': str(out / 'metadata.xml'), 'total_results': total,
                'fetched_at': now.isoformat(), 'coverage': 'Fixed version metadata, not live discovery quality.'}
        _save(out / 'seed.json', seed)
        book['fixed_seed']['sha256'] = hashlib.sha256((out / 'seed.json').read_bytes()).hexdigest()
    except (OSError, ValueError, LiteratureSearchError) as error:
        report['blockers'].append('metadata:' + (error.code if isinstance(error, LiteratureSearchError) else str(error)))
        _save(out / 'report.json', report)
        return report

    from paper_assistant.tools.arxiv.fetch import PaperFetcher, PaperFetchSession
    from paper_assistant.research.task import ResearchTask
    from paper_assistant.tools.knowledge.pdf_parser import parse_pdf
    from pypdf.errors import PyPdfError

    fetcher = PaperFetcher(cache_directory=root / 'data/paper_cache', transport=pdf_transport, gate=gate)
    drafts = {}
    for name, source in book['sources'].items():
        # Each acquisition has its own bounded one-paper session: this is material
        # preparation, not a research run with a three-paper business limit.
        row = by_id[(source['arxiv_id'], source['version'])]
        task = ResearchTask(request.query, (request.query,), request.start_at, request.end_at, target_papers=1)
        result = PaperFetchSession(task, [row], fetcher).fetch(row.arxiv_id)
        report['sources'][name] = result.to_dict()
        source.update(pdf_path=None, pdf_sha256=None, page_count=None, evidence=[],
                      verification_status='pdf_pending')
        if result.asset is None:
            report['blockers'].append(name + ':' + (result.error_code or result.status))
            continue
        try:
            pages = parse_pdf(result.asset.pdf_path, clean=False)
            path = Path(result.asset.pdf_path).resolve().relative_to(root).as_posix()
            source.update(pdf_path=path, pdf_sha256=result.asset.source_sha256, page_count=len(pages),
                          verification_status='pdf_acquired_page_evidence_pending')
            drafts[name] = [{'page_number': p.page_number, 'quote': p.text[:350],
                             'claim': '', 'reviewer': '', 'review_status': 'not_reviewed'}
                            for p in pages if p.text]
            if result.asset.origin != 'live_arxiv_pdf':
                report['blockers'].append(name + ':pdf_not_live')
        except (OSError, ValueError, PyPdfError) as error:
            report['blockers'].append(name + ':pdf_parse_failed:' + type(error).__name__)
    if source_origin != 'live_arxiv_api':
        report['blockers'].append('metadata_not_live')
    report['status'] = 'blocked' if report['blockers'] else 'pending_human_review'
    book['material_status'] = report['status']
    book['metadata_checked_at'] = now.isoformat()
    book['metadata_origin'] = source_origin
    book['metadata_evidence'] = 'Exact-version id_list response preserved in preparation attempt; page excerpts require human semantic review.'
    _save(out / 'sources.prepared.json', book)
    _save(out / 'evidence.draft.json', drafts)
    _save(out / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    report = prepare_materials(args.root, args.out)
    print(json.dumps({'status': report['status'], 'blockers': report['blockers'],
                      'report': str(args.out / 'report.json')}, ensure_ascii=False))
    return 1 if report['blockers'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
