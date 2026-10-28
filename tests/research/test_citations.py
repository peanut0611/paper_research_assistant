import json

import pytest

from paper_assistant.research.citations import CitationEvidence, CitationQuoteError, EvidenceSource, validate_answer


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


def test_invalid_pdf_hyphens_are_rejected_with_literal_repair_examples_for_each_source():
    evidence = {"E1": CitationEvidence("Abstract Evidence constrains the an-swer. Next sentence.", (SOURCE,)),
                "E2": CitationEvidence("The authors measure semantic drift. Other text.", (SOURCE,))}
    raw = output("结论 [E1][E2]", [{"evidence_id": "E1", "quote": "Evidence constrains the answer."},
                               {"evidence_id": "E2", "quote": "The authors prove complete safety."}])
    with pytest.raises(CitationQuoteError) as caught:
        validate_answer(raw, evidence)
    assert {row["evidence_id"] for row in caught.value.errors} == {"E1", "E2"}
    for row in caught.value.errors:
        assert row["example_source_quote"] in evidence[row["evidence_id"]].text
        assert row["example_is_not_claim_validation"]
    repaired = output("据原文修改后的概括 [E1][E2]", [{"evidence_id": row["evidence_id"],
        "quote": row["example_source_quote"]} for row in caught.value.errors])
    assert len(validate_answer(repaired, evidence)[2]) == 2


def test_short_quote_repair_does_not_fall_back_to_author_header_in_long_pdf_text():
    text = "Title and author contact. " * 15 + "Abstract Evidence constrains the an-swer. " + "Other discussion. " * 100
    with pytest.raises(CitationQuoteError) as caught:
        validate_answer(output(citations=[{"evidence_id": "E1", "quote": "Evidence constrains the answer"}]),
                        {"E1": CitationEvidence(text, (SOURCE,))})
    hint = caught.value.errors[0]["example_source_quote"]
    assert hint.startswith("Evidence constrains") and "an-swer" in hint and hint in text
