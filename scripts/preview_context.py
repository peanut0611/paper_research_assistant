"""Retrieve evidence and preview a budgeted prompt without calling an LLM."""

import argparse
import json
import os
from pathlib import Path

from paper_assistant.context_builder import build_context
from paper_assistant.embedding import SentenceTransformerEmbedder, load_dense_index, search_dense_index
from paper_assistant.lexical_retrieval import build_bm25_index, search_bm25_index, search_hybrid_index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--index", type=Path, default=Path("data/index/dense"))
    parser.add_argument("--method", choices=["dense", "bm25", "hybrid"], default="hybrid")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--max-input-tokens", type=int, default=2048)
    parser.add_argument("--output", type=Path, default=Path("data/context/context_preview.json"))
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.top_k <= 0 or args.max_input_tokens <= 0:
        parser.error("top-k and max-input-tokens must be positive")
    if args.output.resolve() in {(args.index / name).resolve() for name in ("embeddings.npy", "chunks.jsonl", "manifest.json")}:
        parser.error("Output cannot overwrite index files")
    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    try:
        index = load_dense_index(args.index)
        embedder = SentenceTransformerEmbedder(index.model_name)
        if args.method == "bm25":
            results = search_bm25_index(build_bm25_index(index.chunks), args.question, top_k=args.top_k)
        elif args.method == "hybrid":
            results = search_hybrid_index(index, build_bm25_index(index.chunks), args.question,
                                          embedder, top_k=args.top_k, candidate_k=max(20, args.top_k))
        else:
            results = search_dense_index(index, args.question, embedder, top_k=args.top_k)
        def preview_counter(messages):
            return sum(len(embedder.tokenizer(
                message.content, add_special_tokens=True, truncation=False, verbose=False,
            )["input_ids"]) for message in messages)
        bundle = build_context(
            args.question, results, preview_counter, max_input_tokens=args.max_input_tokens,
            token_counter_name=f"PREVIEW ONLY: {index.model_name}; counts message texts, not a generation-model chat template",
        )
        payload = {**bundle.to_dict(), "retrieval_method": args.method,
                   "retrieved_chunk_ids": [result.chunk.chunk_id for result in results],
                   "budget_note": "E5 preview estimate only. Before generation, rebuild with the LLM tokenizer/chat template and reserve output tokens."}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    except (ValueError, OSError) as error:
        parser.exit(1, f"Context preview failed: {error}\n")
    print(f"Retrieved chunks: {len(results)}; evidence blocks: {len(bundle.evidence)}")
    print(f"Prompt token estimate: {bundle.prompt_token_count}/{bundle.max_input_tokens}")
    print("PREVIEW ONLY: the actual LLM tokenizer and chat template must be used before generation.")
    for entry in bundle.excluded:
        print(f"EXCLUDED {entry.chunk_id}: {entry.reason}")
    for message in bundle.messages:
        print(f"\n[{message.role}]\n{message.content}")
    print(f"\nSaved: {args.output}")


if __name__ == "__main__":
    main()
