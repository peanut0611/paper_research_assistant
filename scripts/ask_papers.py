"""Retrieve paper evidence and ask GPT-5.4 Mini for a cited answer."""

import argparse
import json
import os
from pathlib import Path

from openai import APIConnectionError, APIStatusError, APITimeoutError

from paper_assistant.answer_generation import OpenAIAnswerGenerator, describe_api_error
from paper_assistant.embedding import SentenceTransformerEmbedder, load_dense_index, search_dense_index
from paper_assistant.lexical_retrieval import build_bm25_index, search_bm25_index, search_hybrid_index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--index", type=Path, default=Path("data/index/dense"))
    parser.add_argument("--method", choices=["dense", "bm25", "hybrid"], default="hybrid")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--max-input-tokens", type=int, default=4096)
    parser.add_argument("--max-output-tokens", type=int, default=512)
    parser.add_argument("--max-cost-usd", type=float, default=0.02)
    parser.add_argument("--token-count-mode", choices=["api", "local"], default="api",
                        help="api: remote exact count (default); local: tiktoken estimate, skips remote counting")
    parser.add_argument("--local-models-only", action="store_true", help="Load cached embedding weights; OpenAI API still needs network access")
    parser.add_argument("--ignore-env-proxy", action="store_true",
                        help="OpenAI transport only: ignore environment/system proxy discovery and certificate environment variables")
    parser.add_argument("--output", type=Path, default=Path("data/answers/latest.json"))
    args = parser.parse_args()
    if not args.question.strip() or args.top_k <= 0:
        parser.error("Question must be non-blank and top-k must be positive")
    if args.output.resolve() in {(args.index / name).resolve() for name in ("embeddings.npy", "chunks.jsonl", "manifest.json")}:
        parser.error("Output cannot overwrite index files")
    if args.local_models_only:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    try:
        # Fail fast before loading embedding weights if the local key is absent.
        generator = OpenAIAnswerGenerator(token_count_mode=args.token_count_mode, trust_env=not args.ignore_env_proxy)
        if args.token_count_mode == "local":
            os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(Path("tmp/tiktoken-cache").resolve()))
        index = load_dense_index(args.index)
        if args.method == "bm25":
            results = search_bm25_index(build_bm25_index(index.chunks), args.question, top_k=args.top_k)
        else:
            embedder = SentenceTransformerEmbedder(index.model_name)
            if args.method == "hybrid":
                results = search_hybrid_index(index, build_bm25_index(index.chunks), args.question,
                                              embedder, top_k=args.top_k, candidate_k=max(20, args.top_k))
            else:
                results = search_dense_index(index, args.question, embedder, top_k=args.top_k)
        run = generator.answer_question(args.question, results,
                                        max_input_tokens=args.max_input_tokens,
                                        max_output_tokens=args.max_output_tokens,
                                        max_cost_usd=args.max_cost_usd)
        payload = {**run.to_dict(), "retrieval_method": args.method,
                   "index_path": str(args.index),
                   "retrieved_chunk_ids": [result.chunk.chunk_id for result in results]}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    except APITimeoutError:
        parser.exit(1, "OpenAI request timed out. No automatic retry; check API usage before retrying.\n")
    except APIConnectionError:
        parser.exit(1, "Cannot connect to OpenAI. Check local network/proxy settings; no automatic retry.\n")
    except APIStatusError as error:
        parser.exit(1, describe_api_error(error) + "\n")
    except (ValueError, OSError) as error:
        parser.exit(1, f"Answer generation failed: {error}\n")
    answer = run.result
    print(f"Model: {answer.model}; status: {answer.status}")
    if answer.answer:
        print(f"\n{answer.answer}")
    for citation in answer.citations:
        source = citation.sources[0]
        print(f"[{citation.evidence_id}] {source.filename}, page {source.page_number}: {citation.quote}")
    if answer.usage:
        print(f"\nTokens: input={answer.usage.input_tokens}, cached={answer.usage.cached_input_tokens}, output={answer.usage.output_tokens}")
        print(f"Estimated generation cost: ${answer.usage.estimated_cost_usd:.6f}")
    print(f"Saved: {args.output}")
    if answer.status in {"invalid_response", "incomplete", "model_refusal"}:
        parser.exit(1, f"Model output was not accepted: {answer.error}\n")


if __name__ == "__main__":
    main()
