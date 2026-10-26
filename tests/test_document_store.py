import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from paper_assistant.documents.chunking import TextChunk
from paper_assistant.documents.ingestion import IngestedDocument, ingest_pdf_directory
from paper_assistant.documents.store import DocumentStore, load_document_store, save_document_store
from paper_assistant.retrieval.embedding import DenseIndex, save_dense_index
from paper_assistant.documents.pdf_parser import PageDocument


def index_for_pages(pages):
    chunks = tuple(TextChunk(
        chunk_id=f"{page.document_id}:{page.page_number}", document_id=page.document_id,
        filename=page.filename, page_number=page.page_number, chunk_index=1,
        word_start=0, word_end=len(page.text.split()), text=page.text,
        char_start=0, char_end=len(page.text),
    ) for page in pages if page.text.strip())
    return DenseIndex(chunks, np.ones((len(chunks), 2), dtype=np.float32) / np.sqrt(2), "fake")


def make_store(texts=("中文 English evidence", "second page", "")):
    pages = [PageDocument("doc", "doc.pdf", number, text) for number, text in enumerate(texts, 1)]
    source = IngestedDocument("doc", "doc.pdf", "doc.pdf", "a" * 64, len(pages), ("Extraction warning",))
    index = index_for_pages(pages)
    return DocumentStore(index, pages, [source]), index, pages, source


def test_catalog_keeps_source_identity_blank_pages_and_extraction_warning():
    store, _, _, _ = make_store()
    doc = store.list_documents()[0]
    assert (doc.document_id, doc.page_count, doc.fulltext_status) == ("doc", 3, "fulltext_ready")
    assert doc.version == "sha256:" + "a" * 64
    assert store.read_pages("doc", 3).pages[0].text == ""
    read = store.read_pages("doc", 1)
    assert read.build_id == store.build_id
    assert read.parse_warnings == ("Extraction warning",)


def test_page_reads_have_exact_offsets_and_can_resume_without_losing_characters():
    store, _, pages, _ = make_store()
    first = store.read_pages("doc", 1, 2, max_chars=4)
    assert first.truncated and first.pages[0].truncated
    assert (first.next_page, first.next_char_start) == (1, 4)
    second = store.read_pages("doc", first.next_page, 2, char_start=first.next_char_start)
    assert second.truncated  # Prefix of first page deliberately excluded.
    assert second.next_page is None
    assert first.pages[0].text + second.pages[0].text == pages[0].text
    assert second.pages[1].text == pages[1].text
    assert second.pages[0].char_start == 4
    boundary = store.read_pages("doc", 1, 2, max_chars=len(pages[0].text))
    assert boundary.truncated and not boundary.pages[0].truncated
    assert (boundary.next_page, boundary.next_char_start) == (2, 0)


@pytest.mark.parametrize("args,kwargs", [
    (("../doc", 1), {}), (("missing", 1), {}), (("doc", 0), {}),
    (("doc", 2, 1), {}), (("doc", 1, 4), {}), (("doc", True), {}),
    (("doc", 1), {"max_chars": 12001}), (("doc", 1), {"max_chars": 0}),
    (("doc", 1), {"char_start": -1}), (("doc", 1), {"char_start": 999}),
])
def test_page_reader_rejects_unknown_ids_and_invalid_ranges(args, kwargs):
    with pytest.raises(ValueError):
        make_store()[0].read_pages(*args, **kwargs)


def test_page_count_limit_cannot_be_bypassed_by_large_char_budget():
    store, _, _, _ = make_store(("text",) * 6)
    with pytest.raises(ValueError, match="at most 5"):
        store.read_pages("doc", 1, 6)


def test_store_rejects_duplicate_ids_missing_pages_and_stale_chunk_offsets():
    _, index, pages, source = make_store()
    with pytest.raises(ValueError, match="Duplicate"):
        DocumentStore(index, pages, [source, source])
    with pytest.raises(ValueError, match="Duplicate page"):
        DocumentStore(index, [*pages, pages[0]], [source])
    with pytest.raises(ValueError, match="Missing page"):
        DocumentStore(index, pages[:-1], [source])
    with pytest.raises(ValueError, match="differs from page"):
        DocumentStore(index, [replace(pages[0], text="changed " + pages[0].text), *pages[1:]], [source])
    with pytest.raises(ValueError, match="character offsets"):
        DocumentStore(replace(index, chunks=(replace(index.chunks[0], char_start=None), *index.chunks[1:])), pages, [source])
    with pytest.raises(ValueError, match="no indexed chunks"):
        DocumentStore(replace(index, chunks=index.chunks[:1], embeddings=index.embeddings[:1]), pages, [source])


def test_document_snapshot_round_trip_and_source_freshness(tmp_path, sample_pdf):
    report = ingest_pdf_directory(sample_pdf.parent)
    index = index_for_pages(report.pages)
    store = DocumentStore(index, report.pages, report.documents)
    output = tmp_path / "index"
    save_dense_index(index, output)
    save_document_store(store, output)
    loaded = load_document_store(output, source_directory=sample_pdf.parent)
    assert loaded.build_id == store.build_id
    assert loaded.read_pages("sample", 1).pages[0].text == report.pages[0].text
    sample_pdf.write_bytes(sample_pdf.read_bytes() + b"\n%changed")
    with pytest.raises(ValueError, match="missing or changed"):
        loaded.verify_sources(sample_pdf.parent)
    # Frozen snapshot can still be replayed without pretending to be current.
    assert load_document_store(output).build_id == store.build_id


@pytest.mark.parametrize("artifact", ["document_store.json", "chunks.jsonl", "embeddings.npy"])
def test_changed_snapshot_chunks_or_vectors_are_rejected(tmp_path, artifact):
    store, index, _, _ = make_store()
    save_dense_index(index, tmp_path)
    save_document_store(store, tmp_path)
    path = tmp_path / artifact
    if artifact == "embeddings.npy":
        changed = index.embeddings.copy()
        changed[0, 0] += 0.25
        np.save(path, changed)
    elif artifact == "chunks.jsonl":
        path.write_text(path.read_text(encoding="utf-8").replace("second page", "changed page"), encoding="utf-8")
    else:
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="checksum|differs|mismatch"):
        load_document_store(tmp_path)


def test_snapshot_cannot_be_attached_to_other_index_or_survive_legacy_rebuild(tmp_path):
    store, index, _, _ = make_store()
    save_dense_index(replace(index, model_name="other-model"), tmp_path)
    with pytest.raises(ValueError, match="Saved index differs"):
        save_document_store(store, tmp_path)
    save_dense_index(index, tmp_path)
    save_document_store(store, tmp_path)
    save_dense_index(index, tmp_path)  # Old callers cannot leave a valid stale binding.
    with pytest.raises(ValueError, match="no bound document store"):
        load_document_store(tmp_path)


def test_source_verification_rejects_path_escape(tmp_path):
    _, index, pages, source = make_store()
    store = DocumentStore(index, pages, [replace(source, source="../outside.pdf")])
    with pytest.raises(ValueError, match="escapes"):
        store.verify_sources(tmp_path)






