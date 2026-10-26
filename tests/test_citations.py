import json

import pytest

from paper_assistant.agent.citations import CitationEvidence, EvidenceSource, validate_answer


SOURCE = EvidenceSource("chunk-1", "new-paper", "new-paper.pdf", 2, 0, 0, 10, 0, 30, 1, .8)
EVIDENCE = {"E1": CitationEvidence("中文证据：ＡＢＣ\n test", (SOURCE,))}


def output(answer="正文结论 [E1]", citations=None, insufficient=False):
    return json.dumps({"answer": answer, "insufficient_evidence": insufficient,
        "citations": [{"evidence_id": "E1", "quote": "ABC test"}] if citations is None else citations})


def test_literal_citation_preserves_task_source_and_normalizes_pdf_whitespace():
    answer, insufficient, citations = validate_answer(output(), EVIDENCE)
    assert answer == "正文结论 [E1]" and not insufficient
    assert citations[0].sources == (SOURCE,)


@pytest.mark.parametrize("raw", [
    output(citations=[{"evidence_id": "E9", "quote": "ABC"}]),
    output(citations=[{"evidence_id": "E1", "quote": "unobserved claim"}]),
    output(citations=[{"evidence_id": "E1", "quote": "ABC"}] * 2),
    output(answer="正文结论 [E2]"),
    output(answer="没有证据", citations=[]),
    json.dumps({"answer": "missing fields"}),
])
def test_unobserved_or_inconsistent_evidence_is_rejected(raw):
    with pytest.raises(ValueError):
        validate_answer(raw, EVIDENCE)


def test_insufficient_evidence_can_have_no_citations():
    assert validate_answer(output("证据不足", [], True), {}) == ("证据不足", True, ())
