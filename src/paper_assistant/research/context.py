"""Research state projection at the model boundary; stored evidence stays intact."""

from paper_assistant.research.state import detached, digest

def _pick(row, keys):
    return {key: row[key] for key in keys if key in row}


def _compact(view, *, abstracts=1200, evidence_chars=1400, evidence_count=6, notes=300, titles=240, history=4, minimal=False):
    candidates = []
    for row in view["candidates"]:
        fields = ("arxiv_id", "version", "document_id", "status", "page_count") if minimal else (
            "arxiv_id", "version", "document_id", "status", "fetch_status", "last_error", "page_count", "submitted_at", "updated_at")
        item = _pick(row, fields)
        item["title"] = row.get("title", "")[:titles]
        item["title_truncated"] = len(row.get("title", "")) > titles
        item["abstract"] = row.get("abstract", "")[:abstracts]
        item["abstract_truncated"] = len(row.get("abstract", "")) > abstracts
        if row.get("status") == "downloaded":
            item["next_action"] = {"tool": "ingest_paper", "arguments": {"arxiv_id": row["arxiv_id"]}}
        candidates.append(item)
    evidence = []
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
        source = _pick(row["source"], ("arxiv_id", "version", "document_id", "source_sha256"))
        evidence.append({"evidence_id": identifier, "source": source, "page_number": row["page_number"],
            "char_start": row["char_start"], "char_end": row["char_end"], "text": row["text"][:evidence_chars],
            "text_truncated": len(row["text"]) > evidence_chars})
    observations = []
    for row in view["observations"][-history:]:
        result = row["result"]
        data = result["data"] or {}
        hint = _pick(data, ("status", "document_id", "required_action", "valid", "insufficient_evidence", "truncated", "next_page", "next_char_start"))
        arguments = detached(row["arguments"])
        if row["tool"] == "verify_citations":
            arguments = {"insufficient_evidence": arguments.get("insufficient_evidence"),
                         "cited_evidence_ids": [citation.get("evidence_id") for citation in arguments.get("citations", [])]}
        elif isinstance(arguments.get("query"), str):
            arguments["query_truncated"] = len(arguments["query"]) > notes
            arguments["query"] = arguments["query"][:notes]
        if row["tool"] == "verify_citations" and result["success"]:
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
        # At the smallest projection keep rejected IDs/decisions without repeating
        # their explanations; the complete notes remain in persisted task state.
        limit = notes if not minimal or row["selected"] else 0
        selections[identifier] = {**row, "reason": row["reason"][:limit], "reason_truncated": len(row["reason"]) > limit}
    return {"task": view["binding"]["task"], "runner_limits": view["binding"]["config"], "tool_limits": view["tool_limits"],
            "usage_so_far": view["usage"], "candidates": candidates, "evidence": evidence,
            "omitted_evidence_count": len(view["evidence"]) - len(evidence), "observations": observations,
            "queries": [{**row, "query": row["query"][:notes], "query_truncated": len(row["query"]) > notes} for row in view["queries"][-history:]],
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

