"""Shared explicit document scope validation, applied before candidate truncation."""

from collections.abc import Sequence

from paper_assistant.documents.chunking import TextChunk


def validate_document_scope(chunks: Sequence[TextChunk], document_ids: Sequence[str] | None) -> frozenset[str] | None:
    if document_ids is None:
        return None
    if isinstance(document_ids, (str, bytes)) or not isinstance(document_ids, Sequence):
        raise ValueError("document_ids must be a sequence of registered IDs")
    if any(not isinstance(identifier, str) or not identifier for identifier in document_ids):
        raise ValueError("Invalid document ID in scope")
    scope = frozenset(document_ids)
    unknown = scope - {chunk.document_id for chunk in chunks}
    if unknown:
        raise ValueError(f"Unknown document IDs in scope: {sorted(unknown)}")
    return scope
