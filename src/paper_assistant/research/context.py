"""Research state projection at the model boundary; stored evidence stays intact."""

import re

from paper_assistant.research.state import detached, digest


def _evidence_window(row, limit, *, prefer_method=False):
    """Return one literal span; skip front matter without changing stored text."""
    text = row["text"]
    start = 0
    if len(text) > limit:
        # PDF cleanup often joins headings to adjacent text. Anchor on a word
        # boundary for English and explicit headings for Chinese documents.
        abstract = re.search(r"\bAbstract\b|摘\s*要", text[:1000], re.IGNORECASE)
        method = re.search(r"(?:^|\s)(?:\d+(?:\.\d+)*\s+)?(?:Methods?|Methodology)\b|(?:\d+\s*)?研究方法", text)
        heading = method if prefer_method else None
        if row["page_number"] == 1 and row["char_start"] == 0 and abstract:
            heading = abstract
        if heading:
            start = min(heading.start(), len(text) - limit)
    end = min(len(text), start + limit)
    return {"char_start": row["char_start"] + start, "char_end": row["char_start"] + end,
            "text": text[start:end], "text_truncated": start > 0 or end < len(text)}

def _pick(row, keys):
    return {key: row[key] for key in keys if key in row}


def _literature_summary(data):
    rows = data.get("candidates", [])
    return {"candidate_count": len(rows), "candidate_ids": [row["arxiv_id"] for row in rows[:10]],
            "candidate_details": "current_context.candidates",
            "next_action": ("Evaluate candidate relevance, record selections, then fetch_paper; downloaded papers need ingest_paper."
                            if rows else "No candidates returned. Rewrite keywords within task dates if search budget remains.")}


def _compact(view, *, abstracts=1200, evidence_chars=1400, evidence_count=6, notes=300, titles=240, history=4, minimal=False):
    candidates = []
    for row in view["candidates"]:
        fields = ("arxiv_id", "version", "document_id", "status", "page_count") if minimal else (
            "arxiv_id", "version", "document_id", "status", "fetch_status", "last_error", "page_count", "submitted_at", "updated_at")
        item = _pick(row, fields)
        item["title"] = row.get("title", "")[:titles]
        if not minimal:
            item["title_truncated"] = len(row.get("title", "")) > titles
        if abstracts or not minimal:
            item["abstract"] = row.get("abstract", "")[:abstracts]
            item["abstract_truncated"] = len(row.get("abstract", "")) > abstracts
        if row.get("status") == "downloaded":
            item["next_action"] = {"tool": "ingest_paper", "arguments": {"arxiv_id": row["arxiv_id"]}}
        candidates.append(item)
    evidence = []
    questions = " ".join(view["binding"]["task"].get("research_questions", []))
    prefer_method = bool(re.search(r"方法|机制|原理|步骤|\b(?:method|approach|algorithm|how)\b", questions, re.IGNORECASE))
    preferred = []
    # Keep the best hit from the latest retrieval, rather than its last/lowest
    # ranked hit merely because that hit was assigned the largest E number.
    for observation in reversed(view["observations"]):
        result = observation["result"]
        if not result["success"] or observation["tool"] not in ("search_documents", "read_pages"):
            continue
        data = result["data"] or {}
        rows = sorted(data.get("results", []), key=lambda row: row["rank"]) if observation["tool"] == "search_documents" else data.get("pages", [])
        for row in rows:
            identifier = row.get("evidence_id")
            if identifier in view["evidence"] and identifier not in preferred:
                preferred.append(identifier)
    fallback = sorted(view["evidence"], key=lambda item: (view["evidence"][item].get("rank", 1), -int(item[1:])))
    selected = (preferred + [identifier for identifier in fallback if identifier not in preferred])[:evidence_count]
    for identifier in selected:
        row = view["evidence"][identifier]
        source = _pick(row["source"], ("document_id",) if minimal else ("arxiv_id", "version", "document_id", "source_sha256"))
        evidence.append({"evidence_id": identifier, "source": source, "page_number": row["page_number"],
                         **_evidence_window(row, evidence_chars, prefer_method=prefer_method)})
    observations = []
    for row in (view["observations"][-history:] if history else []):
        result = row["result"]
        data = result["data"] or {}
        hint = _pick(data, ("status", "document_id", "required_action", "valid", "insufficient_evidence", "answer_kind", "reference_scope", "truncated", "next_page", "next_char_start"))
        if row["tool"] == "search_literature" and result["success"]:
            hint.update(_literature_summary(data))
        arguments = detached(row["arguments"])
        if row["tool"] == "verify_citations":
            arguments = {"insufficient_evidence": arguments.get("insufficient_evidence"),
                         "cited_evidence_ids": [citation.get("evidence_id") for citation in arguments.get("citations", [])]}
        elif isinstance(arguments.get("query"), str):
            arguments["query_truncated"] = len(arguments["query"]) > notes
            arguments["query"] = arguments["query"][:notes]
        if row["tool"] in ("verify_citations", "list_references") and result["success"]:
            hint["answer"] = data["answer"][:max(300, evidence_chars)]
            hint["answer_truncated"] = len(data["answer"]) > max(300, evidence_chars)
        error = detached(result["error"])
        if error and isinstance(error.get("message"), str):
            error["message_truncated"] = len(error["message"]) > notes
            error["message"] = error["message"][:notes]
        observations.append({"tool": row["tool"], "arguments": arguments, "success": result["success"],
                             "error": error, "result_summary": hint})
    selections = {}
    for identifier, row in view["selections"].items():
        # IDs and decisions survive; complete reasons stay in persisted state.
        if minimal:
            selections[identifier] = {"selected": row["selected"]}
        else:
            selections[identifier] = {**row, "reason": row["reason"][:notes], "reason_truncated": len(row["reason"]) > notes}
    return {"task": view["binding"]["task"], "runner_limits": view["binding"]["config"], "tool_limits": view["tool_limits"],
            "usage_so_far": view["usage"], "candidates": candidates, "evidence": evidence,
            "omitted_evidence_count": len(view["evidence"]) - len(evidence), "observations": observations,
            "queries": [{**row, "query": row["query"][:notes], "query_truncated": len(row["query"]) > notes} for row in (view["queries"][-history:] if history else [])],
            "omitted_observation_count": max(0, len(view["observations"]) - history),
            "omitted_query_count": max(0, len(view["queries"]) - history),
            "selections": selections,
            "gaps": [item[:notes] for item in view["gaps"]], "gap_notes_truncated": any(len(item) > notes for item in view["gaps"]),
            "completion": view.get("completion", {}),
            "last_error": ({**view["last_error"], "message": view["last_error"].get("message", "")[:notes]}
                           if view.get("last_error") else None)}


def _context_key(view):
    return digest({name: view.get(name) for name in ("binding", "tool_specs", "tool_limits", "candidates", "evidence", "observations",
                  "queries", "selections", "gaps", "last_error", "usage", "completion", "messages")})

