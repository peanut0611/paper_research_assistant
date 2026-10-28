"""Validate citations to observed task evidence; no model requests or default corpus."""
import json
import re
import unicodedata
from difflib import SequenceMatcher
from collections.abc import Mapping
from dataclasses import dataclass

@dataclass(frozen=True)
class EvidenceSource:
    chunk_id: str
    document_id: str
    filename: str
    page_number: int
    chunk_index: int
    word_start: int
    word_end: int
    char_start: int | None
    char_end: int | None
    retrieval_rank: int
    retrieval_score: float


@dataclass(frozen=True)
class CitationEvidence:
    text: str
    sources: tuple[EvidenceSource, ...]


@dataclass(frozen=True)
class ValidatedCitation:
    evidence_id: str
    quote: str
    sources: tuple[EvidenceSource, ...]


class CitationQuoteError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("Quotes absent from evidence: " + ", ".join(dict.fromkeys(row["evidence_id"] for row in errors))
            + ". Copy exact source text, preserving PDF hyphens and punctuation; reconsider unsupported claims.")


def _quote_hint(identifier, quote, text):
    # This is a repair hint, never an alternate acceptance rule. Every example
    # remains a literal span of the observed source and must be checked again.
    match = SequenceMatcher(None, quote[:500], text, autojunk=False).find_longest_match()
    start = max(0, match.b - match.a) if match.size else 0
    end = text.find(". ", match.b + match.size)
    end = min(len(text), start + 240, end + 1 if end >= start else len(text))
    return {"evidence_id": identifier, "reason": "quote_not_found",
            "example_source_quote": text[start:end], "example_is_not_claim_validation": True}


def normalize_evidence(text: str) -> str:
    """Ignore Unicode variants and PDF whitespace, retaining punctuation."""
    return "".join(unicodedata.normalize("NFKC", text).split())


def validate_answer(raw_output: str, evidence: Mapping[str, CitationEvidence]) -> tuple[str, bool, tuple[ValidatedCitation, ...]]:
    """Verify structure and literal citations, not whether a claim is entailed."""
    data = json.loads(raw_output)
    if not isinstance(data, dict) or set(data) != {"answer", "insufficient_evidence", "citations"}:
        raise ValueError("Answer must contain exactly answer, insufficient_evidence, citations")
    answer = data["answer"]
    insufficient = data["insufficient_evidence"]
    if not isinstance(answer, str) or not answer.strip() or type(insufficient) is not bool:
        raise ValueError("Answer needs non-blank text and a boolean insufficient_evidence")
    if not isinstance(data["citations"], list):
        raise ValueError("citations must be an array")
    citations = []
    quote_errors = []
    seen = set()
    mapping = evidence
    for item in data["citations"]:
        if not isinstance(item, dict) or set(item) != {"evidence_id", "quote"}:
            raise ValueError("Each citation must contain evidence_id and quote")
        identifier, quote = item["evidence_id"], item["quote"]
        if not isinstance(identifier, str) or identifier not in mapping:
            raise ValueError("Answer cites an unknown evidence ID")
        if not isinstance(quote, str) or not normalize_evidence(quote):
            raise ValueError("Citation quote cannot be blank")
        normalized = normalize_evidence(quote)
        if normalized not in normalize_evidence(mapping[identifier].text):
            quote_errors.append(_quote_hint(identifier, quote, mapping[identifier].text) if len(quote_errors) < 3
                                else {"evidence_id": identifier, "reason": "quote_not_found"})
            continue
        if (identifier, normalized) in seen:
            raise ValueError("Duplicate citation")
        seen.add((identifier, normalized))
        citations.append(ValidatedCitation(identifier, quote, mapping[identifier].sources))
    if quote_errors:
        raise CitationQuoteError(quote_errors)
    inline = set(re.findall(r"\[(E[^\]]*)\]", answer))
    if inline != {citation.evidence_id for citation in citations}:
        raise ValueError("Inline evidence IDs and citation list do not agree")
    if not insufficient and not citations:
        raise ValueError("A supported answer must cite at least one evidence source")
    return answer, insufficient, tuple(citations)
