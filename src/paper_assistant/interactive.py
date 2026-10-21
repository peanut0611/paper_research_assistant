"""Local page adapter reusing the existing retrieval and answer pipeline."""

import threading
from pathlib import Path

from paper_assistant.answer_generation import OpenAIAnswerGenerator, create_openai_client
from paper_assistant.embedding import SentenceTransformerEmbedder, load_dense_index, search_dense_index
from paper_assistant.lexical_retrieval import build_bm25_index, search_bm25_index, search_hybrid_index


class LocalPaperSession:
    """Cache an index/model, but never share questions or answers across users."""

    def __init__(self, index_path: Path) -> None:
        self.index = load_dense_index(index_path)
        self.bm25 = build_bm25_index(self.index.chunks)
        self._embedder = None
        self._lock = threading.Lock()

    def ask(self, question: str, *, method: str = "hybrid", top_k: int = 3,
            token_count_mode: str = "local") -> dict:
        if not isinstance(question, str) or not question.strip() or len(question) > 4000:
            raise ValueError("请输入 1–4000 个字符的问题。")
        if method not in ("dense", "bm25", "hybrid") or type(top_k) is not int or not 1 <= top_k <= 10:
            raise ValueError("检索方式或片段数量无效。")
        if token_count_mode not in ("api", "local"):
            raise ValueError("计数方式无效。")
        if not self._lock.acquire(blocking=False):
            raise ValueError("已有问题正在处理，请稍后再试。")
        try:
            # Fail before loading model weights when the key is not configured.
            with create_openai_client() as client:
                question = question.strip()
                if method == "bm25":
                    results = search_bm25_index(self.bm25, question, top_k=top_k)
                else:
                    if self._embedder is None:
                        self._embedder = SentenceTransformerEmbedder(self.index.model_name)
                    if method == "hybrid":
                        results = search_hybrid_index(self.index, self.bm25, question, self._embedder,
                                                      top_k=top_k, candidate_k=max(20, top_k))
                    else:
                        results = search_dense_index(self.index, question, self._embedder, top_k=top_k)
                generator = OpenAIAnswerGenerator(client=client, token_count_mode=token_count_mode)
                run = generator.answer_question(question, results)
            return {**run.to_dict(), "retrieval_method": method,
                    "retrieved_chunk_ids": [result.chunk.chunk_id for result in results]}
        finally:
            self._lock.release()
