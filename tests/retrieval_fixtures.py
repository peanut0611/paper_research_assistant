"""Synthetic retrieval results shared by current Agent component tests."""
from paper_assistant.tools.knowledge.chunking import TextChunk
from paper_assistant.tools.knowledge.embedding import SearchResult


def result(rank, text="retrieval", doc="a", page=1):
    return SearchResult(rank, 0.5, TextChunk(
        f"{doc}-{page}-{rank}", doc, f"{doc}.pdf", page, rank, 0, len(text.split()), text,
    ))
