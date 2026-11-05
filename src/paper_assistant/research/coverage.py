"""Selected-paper acquisition/read gaps, not semantic support or paper quotas."""


def selected_paper_coverage(state):
    observed = {row['source']['document_id'] for row in state.evidence.values()}
    documents = {row['arxiv_id']: row['document_id'] for row in state.candidates}
    inspected, ready, failed = set(), set(), set()
    for observation in state.observations:
        tool, args, success = observation['tool'], observation['arguments'], observation['result']['success']
        if tool in ('fetch_paper', 'ingest_paper') and not success:
            failed.add(args.get('arxiv_id'))
        if tool == 'ingest_paper' and success and args.get('arxiv_id') in documents:
            ready.add(documents[args['arxiv_id']])
        elif tool == 'read_pages':
            inspected.add(args.get('document_id'))
        elif tool == 'search_documents':
            # An unscoped search covers only papers indexed at that moment.
            scope = args.get('document_ids')
            inspected.update((ready if success else ()) if scope is None else scope)
    coverage = []
    for row in state.candidates:
        identifier, document = row['arxiv_id'], row['document_id']
        if not state.selections.get(identifier, {}).get('selected'):
            continue
        item = {'arxiv_id': identifier, 'document_id': document, 'status': row['status'],
                'body_evidence_observed': document in observed}
        # An attempted read with no hits or an acquisition failure is a real gap;
        # it is not permission to retry, and must not force more papers.
        item['read_attempted'] = document in inspected
        item['acquisition_failed'] = identifier in failed
        if document not in observed and document not in inspected and identifier not in failed:
            if row['status'] in ('metadata_only', 'abstract_only'):
                item.update(next_tool='fetch_paper', arguments={'arxiv_id': identifier}, remaining_calls=4)
            elif row['status'] == 'downloaded':
                item.update(next_tool='ingest_paper', arguments={'arxiv_id': identifier}, remaining_calls=3)
            elif row['status'] == 'fulltext_ready':
                item.update(next_tool='search_documents', arguments={'document_ids': [document]}, remaining_calls=2)
        coverage.append(item)
    return coverage
