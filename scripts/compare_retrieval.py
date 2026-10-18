"""Compare Dense, bilingual BM25, and equal-weight RRF on identical chunks."""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from paper_assistant.embedding import SentenceTransformerEmbedder, load_dense_index, search_dense_index
from paper_assistant.evaluation import file_sha256, load_questions, verify_metadata, write_evaluation_report
from paper_assistant.lexical_retrieval import TOKENIZER_VERSION, build_bm25_index, search_bm25_index, search_hybrid_index
from paper_assistant.retrieval_comparison import compare_retrievers


def comparison_markdown(report: dict) -> str:
    lines = ["# Dense / BM25 / Hybrid 检索对比", "",
             f"按预先固定指标排序的第一名：**{report['selection']['method']}**。",
             f"中英文通用的使用建议：**{report['bilingual_recommendation']['method']}**。仅代表本次数据，不是独立测试结论。", "",
             "保持 PDF、标注、分块和向量模型不变；三种方法均使用同一索引中的文本块。",
             "未通过标注调参；无答案题不计入相关性均值。", "",
             "## 可回答题逐题平均", "",
             "| 方法 | 证据 Hit@1 | 证据 Hit@3 | 完整证据@1 | 完整证据@3 | 证据 MRR@5 | 平均延迟 ms | P95 ms |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, data in report["methods"].items():
        s = data["summary"]
        m = s["metrics"]
        t = s["latency_all_queries_ms"]
        lines.append(f"| {name} | {m['evidence_hit@1']:.2%} | {m['evidence_hit@3']:.2%} | {m['complete_evidence@1']:.2%} | {m['complete_evidence@3']:.2%} | {m['evidence_mrr@5']:.4f} | {t['mean']:.3f} | {t['p95']:.3f} |")
    lines += ["", "## 按事实组平均（优选依据）", "",
              "| 方法 | 证据 Hit@1 | 证据 MRR@5 | 完整证据@3 |",
              "| --- | ---: | ---: | ---: |"]
    for name, data in report["methods"].items():
        m = data["summary"]["group_macro_metrics"]
        lines.append(f"| {name} | {m['evidence_hit@1']:.2%} | {m['evidence_mrr@5']:.4f} | {m['complete_evidence@3']:.2%} |")
    lines += ["", "指标排序按上表三项从左到右依次比较；完全相同时才比较平均延迟。",
              "中英文使用建议另加约束：各语言证据 Hit@1 和 Hit@3 均不低于 Dense，再按同样指标排序。",
              "这项建议依据观测到的语言差异给出，与实验前固定的指标优胜者分别报告。", "",
              "## 分语言的可回答题", "",
              "| 方法 | 语言 | 题数 | 证据 Hit@1 | 证据 MRR@5 | 完整证据@3 |",
              "| --- | --- | ---: | ---: | ---: | ---: |"]
    for name, data in report["methods"].items():
        for language, subset in data["summary"]["by_language"].items():
            m = subset["metrics"]
            lines.append(f"| {name} | {language} | {subset['question_count']} | {m['evidence_hit@1']:.2%} | {m['evidence_mrr@5']:.4f} | {m['complete_evidence@3']:.2%} |")
    lines += ["", "## 相对 Dense 的变化", ""]
    for name, changes in report["changes_vs_dense"].items():
        for metric, ids in changes.items():
            lines.append(f"- {name} / {metric}：改善 {', '.join(ids['improved']) or '无'}；退步 {', '.join(ids['regressed']) or '无'}。")
    lines += ["", "## 配置与边界", "",
              f"- 实验参数：`{json.dumps(report['run']['parameters'], ensure_ascii=False)}`。",
              "- BM25 使用正 IDF，中文单字+双字、英文单词词项；不是词典分词。去除汉字之间的空格，无翻译、词干化或标注词典。",
              "- BM25 零匹配返回空列表；不把零分文档作为命中。英文问题可能无法匹配中文正文。",
              "- Hybrid 先分别召回候选，再等权 RRF；BM25 没有候选时，结果顺序退回 Dense。RRF 同分保留先出现顺序（Dense 列表在前）。",
              "- 延迟包括每次查询的实际检索，模型加载和索引构建不计入；无查询向量缓存。各轮轮换方法执行顺序。",
              f"- 当前索引有 {report['run']['index_manifest']['chunk_count']} 个块；K 达到块数时，Dense 返回全部语料。样本量与分语言题数见上表。",
              "- 标注仍待人工复核，没有独立测试集。本次优选需在真实论文及新问题上再次验证。",
              "- 未改变默认查询方法。单次报告与完整片段见 dense.json / bm25.json / hybrid.json；详细对比见 comparison.json。", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, default=Path("eval_data/retrieval_questions.jsonl"))
    parser.add_argument("--index", type=Path, default=Path("data/index/dense"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/evaluation/retrieval_comparison"))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--candidate-k", type=int, default=20)
    parser.add_argument("--rrf-constant", type=float, default=60)
    parser.add_argument("--device", default=None)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.repeats <= 0 or args.candidate_k < 5 or not 0 < args.rrf_constant < float("inf"):
        parser.error("Require repeats > 0, candidate-k >= 5, and a finite positive RRF constant")
    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    try:
        questions = load_questions(args.questions)
        metadata_path = args.questions.with_suffix(".meta.json")
        metadata = verify_metadata(args.questions, metadata_path, Path.cwd()) if metadata_path.exists() else None
        dense = load_dense_index(args.index)
        available = {(c.document_id, c.page_number) for c in dense.chunks}
        if any((s.document_id, s.page_number) not in available for q in questions for s in q.relevant_sources):
            raise ValueError("Annotated source page is absent from the index")
        lexical = build_bm25_index(dense.chunks)
        embedder = SentenceTransformerEmbedder(dense.model_name, device=args.device)
        report = compare_retrievers(questions, {
            "dense": lambda query, k: search_dense_index(dense, query, embedder, top_k=k),
            "bm25": lambda query, k: search_bm25_index(lexical, query, top_k=k),
            "hybrid": lambda query, k: search_hybrid_index(dense, lexical, query, embedder, top_k=k, candidate_k=args.candidate_k, rrf_constant=args.rrf_constant),
        }, repeats=args.repeats)
        report["run"] = {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "questions_path": str(args.questions), "questions_sha256": file_sha256(args.questions),
            "annotation_metadata": metadata, "index_path": str(args.index),
            "index_manifest": json.loads((args.index / "manifest.json").read_text(encoding="utf-8")),
            "index_file_sha256": {name: file_sha256(args.index / name) for name in ("manifest.json", "chunks.jsonl", "embeddings.npy")},
            "implementation_sha256": {name: file_sha256(Path(__file__).resolve().parents[1] / "src/paper_assistant" / name) for name in ("embedding.py", "lexical_retrieval.py", "evaluation.py", "retrieval_comparison.py")},
            "device": embedder.device,
            "parameters": {"bm25_k1": lexical.k1, "bm25_b": lexical.b, "tokenizer": TOKENIZER_VERSION,
                           "rrf_constant": args.rrf_constant, "hybrid_candidate_k": args.candidate_k,
                           "repeats": args.repeats, "ks": [1, 3, 5]},
        }
        for name, data in report["methods"].items():
            data["run"] = {**report["run"], "method": name}
            write_evaluation_report(data, args.output_dir / f"{name}.json")
        write_evaluation_report(report, args.output_dir / "comparison.json")
        text = comparison_markdown(report)
        (args.output_dir / "comparison.md").write_text(text, encoding="utf-8")
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f"Comparison failed: {error}\n")
    print(text)
    print(f"Report directory: {args.output_dir}")


if __name__ == "__main__":
    main()
