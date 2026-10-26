"""Task-bound tool protocol and recorded observations, without a model loop.

The caller supplies trusted sessions/workspace once. Model-visible arguments can
never select another task, filesystem path, transport, URL or arbitrary callable.
Recorded replay returns observations only and never re-executes an action.
"""

import hashlib
import json
import re
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from paper_assistant.agent.citations import CitationEvidence, EvidenceSource, validate_answer
from paper_assistant.literature.search import ArxivSearchClient, LiteratureSearchError, LiteratureSearchSession, PaperCandidate
from paper_assistant.literature.fetcher import PaperFetchSession
from paper_assistant.agent.workspace import ResearchWorkspace, WorkspaceError
from paper_assistant.storage import atomic_write_bytes


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _copy(value: Any) -> Any:
    return json.loads(_json(value))


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        sensitive = {"api_key", "openai_api_key", "authorization", "password", "secret", "access_token", "refresh_token"}
        return {str(_redact(key)): "[REDACTED]" if str(key).lower() in sensitive else _redact(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", value)
        return re.sub(r"(?i)\bBearer\s+[^\s\"']+", "Bearer [REDACTED]", value)
    return value


class ToolError(Exception):
    def __init__(self, code: str, message: str, *, data: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


def _validate(value: Any, schema: dict) -> None:
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
            raise ToolError("invalid_arguments", "Arguments must be a JSON object")
        properties = schema["properties"]
        if set(value) - properties.keys() or not set(schema.get("required", ())) <= value.keys():
            raise ToolError("invalid_arguments", "Unknown or missing argument fields")
        for key, item in value.items():
            _validate(item, properties[key])
    elif kind == "array":
        if not isinstance(value, list) or len(value) > schema.get("maxItems", 100):
            raise ToolError("invalid_arguments", "Invalid array argument")
        for item in value:
            _validate(item, schema["items"])
    elif kind == "string":
        if not isinstance(value, str) or not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 4000):
            raise ToolError("invalid_arguments", "Invalid string argument")
        if schema.get("pattern") and re.fullmatch(schema["pattern"], value) is None:
            raise ToolError("invalid_arguments", "Invalid identifier format")
    elif kind == "integer":
        if type(value) is not int or not schema.get("minimum", 0) <= value <= schema.get("maximum", 100):
            raise ToolError("invalid_arguments", "Invalid integer argument")
    elif kind == "boolean":
        if type(value) is not bool:
            raise ToolError("invalid_arguments", "Invalid boolean argument")
    else:
        raise RuntimeError("Unsupported internal tool schema")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolError("invalid_arguments", "Argument is outside its allowed values")


def _object(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def _string(maximum: int = 160) -> dict:
    return {"type": "string", "minLength": 1, "maxLength": maximum}


def _integer(minimum: int, maximum: int) -> dict:
    return {"type": "integer", "minimum": minimum, "maximum": maximum}


class ResearchTools:
    """One explicit tool session per workspace, with a persistent event journal.

    Reopening requires explicit resume of a complete version-2 checkpoint.
    Interrupted tool outcomes are never silently retried or given fresh budgets.
    Use RecordedToolReplay to inspect the exact saved observations without I/O.
    """

    def __init__(self, workspace: ResearchWorkspace, *, search_session: LiteratureSearchSession | None = None,
                 offline: bool = False, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 monotonic: Callable[[], float] = time.monotonic, resume: bool = False) -> None:
        self.workspace, self.task = workspace, workspace.task
        self.offline, self.now, self.monotonic = offline, now, monotonic
        self.journal_path = workspace.directory / "tool_trace.json"
        if self.journal_path.exists() and not resume:
            raise ValueError("A tool journal already exists; replay it or start a new task, do not reset its budgets")
        if resume and not self.journal_path.exists():
            raise ValueError("No tool journal to resume")
        self.search = search_session if search_session is not None else LiteratureSearchSession(self.task, ArxivSearchClient())
        if self.search.task != self.task:
            raise ValueError("Search session belongs to another research task")
        self.search.register_observed(workspace.registered_candidates)
        self.fetch = PaperFetchSession(self.task, workspace.registered_candidates, workspace.fetcher)
        self.fetch.restore_downloaded_ids([row["arxiv_id"] for row in workspace.list_documents() if row["source_sha256"] is not None])
        self._calls = self._local_searches = 0
        self._events: list[dict] = []
        self._evidence: dict[str, dict] = {}
        self._evidence_keys: dict[str, str] = {}
        self._lock = threading.RLock()
        self._journal_failed = False
        self._deadline = None
        self._network_requests = 0
        self._schemas = self._build_specs()
        if resume:
            self._restore()

    def _build_specs(self) -> dict:
        identifier = _string()
        scope = {"type": "array", "maxItems": self.task.limits.max_fulltext_papers, "items": identifier}
        citation = _object({"evidence_id": {**_string(64), "pattern": r"E[1-9][0-9]*"}, "quote": _string(4000)}, ("evidence_id", "quote"))
        return {
            "search_literature": ("Search arXiv within this task's fixed submission window; metadata only.", _object({
                "query": _string(500), "max_results": _integer(1, 10),
                "sort_by": {**_string(), "enum": ["submittedDate", "relevance", "lastUpdatedDate"]}}, ("query",))),
            "fetch_paper": ("Acquire only a registered pinned PDF/version; not reading or indexing.", _object({
                "arxiv_id": identifier, "version": _integer(1, 100000)}, ("arxiv_id",))),
            "ingest_paper": ("Index one registered paper whose acquisition succeeded in this task.", _object({"arxiv_id": identifier}, ("arxiv_id",))),
            "list_documents": ("List this task's candidates, acquisition and fulltext states.", _object({})),
            "search_documents": ("Retrieve fulltext evidence from this task; explicit empty scope searches nothing.", _object({
                "query": _string(4000), "method": {**_string(), "enum": ["dense", "bm25", "hybrid"]},
                "top_k": _integer(1, 10), "document_ids": scope}, ("query",))),
            "read_pages": ("Read bounded physical PDF pages from this task, with exact cleaned-text offsets.", _object({
                "document_id": identifier, "start_page": _integer(1, self.workspace.config.max_pdf_pages),
                "end_page": _integer(1, self.workspace.config.max_pdf_pages),
                "max_chars": _integer(1, self.task.limits.max_read_chars), "char_start": _integer(0, self.workspace.config.max_text_chars)},
                ("document_id", "start_page"))),
            "verify_citations": ("Check only citations to evidence actually returned in this tool session; not semantic entailment.", _object({
                "answer": _string(12000), "insufficient_evidence": {"type": "boolean"},
                "citations": {"type": "array", "maxItems": 40, "items": citation}}, ("answer", "insufficient_evidence", "citations"))),
        }

    def tool_specs(self) -> list[dict]:
        return _copy([{"name": name, "description": description, "parameters": schema}
                      for name, (description, schema) in self._schemas.items()])

    def limits(self) -> dict:
        return {"tool_calls": self._calls, "max_tool_calls": self.task.limits.max_tool_calls,
                "search_rounds": self.search.search_rounds, "max_search_rounds": self.task.limits.max_search_rounds,
                "observed_candidates": len(self.search.observed_candidates), "max_candidates": self.task.limits.max_candidates,
                "fetch_attempts": self.fetch.fetch_attempts, "max_fetch_attempts": self.task.limits.max_fetch_attempts,
                "downloaded_papers": len(self.fetch.downloaded_ids),
                "max_papers": min(self.task.target_papers, self.task.limits.max_fulltext_papers),
                "local_searches": self._local_searches, "max_local_searches": self.task.limits.max_local_searches,
                "network_requests": self._network_requests,
                "max_pages_per_read": self.task.limits.max_pages_per_read, "max_read_chars": self.task.limits.max_read_chars,
                "runtime_and_model_budget": "managed_by_runner_when_attached"}

    def _checkpoint(self) -> dict:
        return {"task": self.task.to_dict(), "offline": self.offline,
                "workspace_sha256": hashlib.sha256(_json(json.loads(self.workspace.manifest_path.read_bytes()))).hexdigest(),
                "registered_candidates": [row.to_dict() for row in self.workspace.registered_candidates],
                "observed_candidates": [row.to_dict() for row in self.search.observed_candidates],
                "search_rounds": self.search.search_rounds, "fetch_attempts": self.fetch.fetch_attempts,
                "downloaded_ids": list(self.fetch.downloaded_ids), "calls": self._calls,
                "local_searches": self._local_searches, "network_requests": self._network_requests,
                "evidence": self._evidence, "evidence_keys": self._evidence_keys}

    @staticmethod
    def read_checkpoint(path: Path) -> dict:
        """Validate a completed journal before trusted workspace reconstruction."""
        if path.stat().st_size > 16_000_000:
            raise ValueError("Tool checkpoint is too large")
        trace = json.loads(path.read_bytes())
        RecordedToolReplay(trace)
        checkpoint = trace.get("checkpoint")
        binding = {"task_id": trace.get("task_id"), "checkpoint": checkpoint,
                   "last_event_sha256": trace["events"][-1]["sha256"] if trace["events"] else None}
        if trace.get("schema_version") != 2 or trace.get("checkpoint_sha256") != hashlib.sha256(_json(binding)).hexdigest():
            raise ValueError("No valid resumable tool checkpoint")
        return trace

    def _restore(self) -> None:
        validated = self.read_checkpoint(self.journal_path)
        checkpoint = validated["checkpoint"]
        if (validated["task_id"] != self.workspace.task_id or checkpoint["task"] != self.task.to_dict()
                or checkpoint["offline"] != self.offline
                or checkpoint["workspace_sha256"] != hashlib.sha256(_json(json.loads(self.workspace.manifest_path.read_bytes()))).hexdigest()
                or _json(checkpoint["registered_candidates"]) != _json([row.to_dict() for row in self.workspace.registered_candidates])):
            raise ValueError("Tool checkpoint belongs to another task, workspace state, candidate set or transport mode")
        self.search.register_observed(tuple(PaperCandidate.from_dict(row) for row in checkpoint["observed_candidates"]))
        self.search.restore_rounds(checkpoint["search_rounds"])
        self.fetch.restore_attempts(checkpoint["fetch_attempts"])
        self.fetch.restore_downloaded_ids(checkpoint["downloaded_ids"])
        for field, cap in (("calls", self.task.limits.max_tool_calls), ("local_searches", self.task.limits.max_local_searches)):
            if type(checkpoint[field]) is not int or not 0 <= checkpoint[field] <= cap:
                raise ValueError("Invalid tool checkpoint accounting")
        self._calls, self._local_searches = checkpoint["calls"], checkpoint["local_searches"]
        self._network_requests = checkpoint["network_requests"]
        self._evidence, self._evidence_keys = checkpoint["evidence"], checkpoint["evidence_keys"]
        self._events = validated["events"]

    def evidence_records(self) -> dict:
        return _copy(self._evidence)

    def _persist(self) -> None:
        trace = self.export_trace()
        atomic_write_bytes(self.journal_path, _json(trace))

    def call(self, tool: str, arguments: dict, *, purpose: str = "", deadline: float | None = None) -> dict:
        with self._lock:
            before = self.limits()
            if self._journal_failed:
                return {"success": False, "data": None,
                        "error": {"code": "audit_storage_error", "message": "Tool journal is unavailable; this session is stopped"},
                        "warnings": [], "task_id": self.workspace.task_id, "event_id": None,
                        "limits": {"before": before, "after": before}}
            started = self.monotonic()
            self._deadline = deadline
            network_before = self.search.client.network_requests + self.workspace.fetcher.network_requests
            event = {"schema_version": 1, "event_id": len(self._events) + 1, "task_id": self.workspace.task_id,
                     "tool": _redact(tool[:80]) if isinstance(tool, str) else "invalid_tool", "arguments": {"rejected": True},
                     "purpose": _redact(purpose[:200]) if isinstance(purpose, str) else "", "started_at": self.now().isoformat(),
                     "status": "started", "limits_before": before, "result": None}
            self._events.append(event)
            # An interruption after this write is observable; no automatic replay
            # can mistake an unknown network outcome for a safe retry.
            try:
                self._persist()
            except OSError:
                self._journal_failed = True
                return {"success": False, "data": None,
                        "error": {"code": "audit_storage_error", "message": "Could not record the tool attempt; no action was executed"},
                        "warnings": [], "task_id": self.workspace.task_id, "event_id": event["event_id"],
                        "limits": {"before": before, "after": before}}
            warnings: list[str] = []
            data = error = None
            try:
                if self._calls >= self.task.limits.max_tool_calls:
                    raise ToolError("tool_budget_exhausted", "Tool-call limit reached")
                self._calls += 1
                if deadline is not None and self.monotonic() >= deadline:
                    raise ToolError("runtime_budget_exhausted", "Task deadline reached before dispatch")
                if not isinstance(tool, str) or tool not in self._schemas:
                    raise ToolError("unknown_tool", "Tool is not registered")
                if not isinstance(purpose, str) or len(purpose) > 200:
                    raise ToolError("invalid_arguments", "Purpose must be a short visible action description")
                _validate(arguments, self._schemas[tool][1])
                # JSON round-trip prevents caller mutations after validation.
                arguments = _copy(arguments)
                event["arguments"] = _redact(arguments)
                self._persist()
                data, warnings = self._dispatch(tool, arguments)
            except ToolError as caught:
                error = {"code": caught.code, "message": str(caught)}
                data = caught.data
            except (LiteratureSearchError, WorkspaceError) as caught:
                error = {"code": caught.code, "message": str(caught)}
            except ValueError:
                error = {"code": "invalid_arguments", "message": "Arguments are invalid for the current task state"}
            except OSError:
                error = {"code": "storage_error", "message": "Tool state or artifacts could not be read or written"}
            except Exception:
                error = {"code": "tool_execution_failed", "message": "Tool failed; no automatic retry was made"}
            self._network_requests += self.search.client.network_requests + self.workspace.fetcher.network_requests - network_before
            result = {"success": error is None, "data": data, "error": error, "warnings": warnings,
                      "task_id": self.workspace.task_id, "event_id": event["event_id"],
                      "limits": {"before": before, "after": self.limits()}}
            event.update(status="completed", duration_ms=round(max(0, self.monotonic() - started) * 1000, 3),
                         limits_after=self.limits(), result=_redact(result),
                         source_refs=[record["source"] for record in self._evidence.values()])
            event["sha256"] = hashlib.sha256(_json(event)).hexdigest()
            try:
                self._persist()
            except OSError:
                self._journal_failed = True
                result.update(success=False, error={"code": "audit_storage_error", "message": "Action may have completed, but its final journal write failed; no automatic retry"})
            return _copy(result)

    def _dispatch(self, tool: str, args: dict) -> tuple[dict, list[str]]:
        warnings: list[str] = []
        if tool == "search_literature":
            result = self.search.search(**args, offline=self.offline, timeout=self._request_timeout(30))
            existing = {candidate.arxiv_id: candidate for candidate in self.workspace.registered_candidates}
            accepted = []
            for candidate in result.candidates:
                if candidate.version is None:
                    warnings.append(f"No pinned version for {candidate.arxiv_id}; fulltext acquisition is unavailable")
                elif candidate.arxiv_id in existing and existing[candidate.arxiv_id].version != candidate.version:
                    warnings.append(f"Kept task-pinned version for {candidate.arxiv_id}; the source returned another version")
                else:
                    accepted.append(candidate)
            self.workspace.register_candidates(accepted)
            self.fetch.register_candidates(self.workspace.registered_candidates)
            pinned = {row.arxiv_id: row.version for row in self.workspace.registered_candidates}
            rows = []
            for candidate in result.candidates:
                row = candidate.to_dict()
                row.pop("raw_entry_xml")
                row["task_pinned_version"] = pinned.get(candidate.arxiv_id)
                rows.append(row)
            return {"candidates": rows, "request": result.request.to_dict(), "total_results": result.total_results,
                    "excluded_date_ids": list(result.excluded_date_ids), "from_cache": result.from_cache,
                    "stale_cache": result.stale_cache, "snapshot_origin": result.snapshot_origin,
                    "raw_sha256": result.raw_sha256, "coverage": "one bounded response, not exhaustive"}, warnings
        if tool == "fetch_paper":
            if args["arxiv_id"] not in {candidate.arxiv_id for candidate in self.workspace.registered_candidates}:
                raise ToolError("unknown_candidate", "Candidate is not registered in this task")
            result = self.fetch.fetch(**args, offline=self.offline, timeout=self._request_timeout(45))
            self.workspace.record_fetch_result(result)
            if result.status != "downloaded":
                raise ToolError(result.error_code, result.error_message, data=result.to_dict())
            data = result.to_dict()
            data["asset"].pop("pdf_path")
            return data, warnings
        if tool == "ingest_paper":
            row = next((row for row in self.workspace.list_documents() if row["arxiv_id"] == args["arxiv_id"]), None)
            if row is None:
                raise ToolError("unknown_candidate", "Candidate is not registered in this task")
            if row["source_sha256"] is None:
                raise ToolError("not_acquired", "Fetch this paper in the task before indexing it")
            result = self.workspace.ingest_paper(**args)
            if result["status"] != "fulltext_ready":
                raise ToolError(result["error_code"], "Paper ingestion failed; inspect the document status", data=result)
            return result, warnings
        if tool == "list_documents":
            rows = self.workspace.list_documents()
            for row in rows:
                for key in ("source", "artifact"):
                    row.pop(key, None)
            return {"documents": rows}, warnings
        if tool == "search_documents":
            if self._local_searches >= self.task.limits.max_local_searches:
                raise ToolError("local_search_budget_exhausted", "Task local-search limit reached")
            self._local_searches += 1
            scope = args.get("document_ids")
            if scope is None:
                rows = self.workspace.list_documents()
                if rows and not any(row["status"] == "fulltext_ready" for row in rows):
                    scope = [row["document_id"] for row in rows if row["status"] == "downloaded"]
            self._require_indexed(scope or ())
            data = self.workspace.search_documents(**args)
            for row in data["results"]:
                chunk = row["chunk"]
                row["evidence_id"] = self._remember(chunk["text"], row["source"], chunk["page_number"],
                    chunk["char_start"], chunk["char_end"], data["build_id"], chunk["filename"],
                    chunk["chunk_id"], chunk["chunk_index"], row["rank"], row["score"])
            return data, warnings
        if tool == "read_pages":
            self._require_indexed((args["document_id"],))
            end = args.get("end_page", args["start_page"])
            if end < args["start_page"] or end - args["start_page"] + 1 > self.task.limits.max_pages_per_read:
                raise ToolError("invalid_arguments", "Page range exceeds this task's read limit")
            args.setdefault("max_chars", min(6000, self.task.limits.max_read_chars))
            data = self.workspace.read_pages(**args)
            for page in data["pages"]:
                page["evidence_id"] = self._remember(page["text"], data["source"], page["page_number"],
                    page["char_start"], page["char_end"], data["build_id"], data["filename"],
                    f"{data['document_id']}::page-{page['page_number']:04d}::read-{page['char_start']}-{page['char_end']}", 0, 1, 0.0) if page["text"].strip() else None
            return data, warnings
        if tool == "verify_citations":
            evidence = {}
            for identifier, row in self._evidence.items():
                source = EvidenceSource(row["chunk_id"], row["source"]["document_id"], row["filename"], row["page_number"],
                    row["chunk_index"], 0, len(row["text"].split()), row["char_start"], row["char_end"], row["rank"], row["score"])
                evidence[identifier] = CitationEvidence(row["text"], (source,))
            try:
                answer, insufficient, citations = validate_answer(_json(args).decode(), evidence)
            except ValueError as error:
                raise ToolError("citation_validation_failed", str(error)) from error
            return {"valid": True, "answer": answer, "insufficient_evidence": insufficient,
                    "citations": [{**asdict(citation), "source": self._evidence[citation.evidence_id]["source"]} for citation in citations],
                    "validation_scope": "ID existence, inline/list agreement, literal quote containment; not semantic entailment"}, warnings
        raise ToolError("unknown_tool", "Tool is not registered")

    def _require_indexed(self, document_ids) -> None:
        """Explain a missing ingestion step without performing unrequested I/O."""
        rows = {row["document_id"]: row for row in self.workspace.list_documents()}
        for identifier in document_ids:
            row = rows.get(identifier)
            if row and row["status"] == "downloaded":
                raise ToolError("document_not_indexed", "PDF is downloaded but not indexed. Call ingest_paper before searching or reading it.",
                                data={"document_id": identifier, "status": row["status"],
                                      "required_action": {"tool": "ingest_paper", "arguments": {"arxiv_id": row["arxiv_id"]}}})

    def _remember(self, text: str, source: dict, page: int, start: int, end: int, build: str,
                  filename: str, chunk_id: str, chunk_index: int, rank: int, score: float) -> str:
        key = hashlib.sha256(_json({"source": source, "page": page, "start": start, "end": end, "text": text})).hexdigest()
        if key not in self._evidence_keys:
            identifier = f"E{len(self._evidence) + 1}"
            self._evidence_keys[key] = identifier
            self._evidence[identifier] = {"text": text, "source": source, "page_number": page, "char_start": start, "char_end": end,
                "build_id": build, "filename": filename, "chunk_id": chunk_id, "chunk_index": chunk_index, "rank": rank, "score": score}
        return self._evidence_keys[key]

    def export_trace(self) -> dict:
        checkpoint = _redact(self._checkpoint())
        binding = {"task_id": self.workspace.task_id, "checkpoint": checkpoint,
                   "last_event_sha256": self._events[-1].get("sha256") if self._events else None}
        return _copy({"schema_version": 2, "task_id": self.workspace.task_id, "events": self._events,
                      "checkpoint": checkpoint, "checkpoint_sha256": hashlib.sha256(_json(binding)).hexdigest()})

    def _request_timeout(self, default: float) -> float:
        remaining = default if self._deadline is None else min(default, self._deadline - self.monotonic())
        if remaining <= 0:
            raise ToolError("runtime_budget_exhausted", "Task deadline reached")
        return remaining


class RecordedToolReplay:
    """Verify and replay recorded JSON observations, never tool side effects."""

    def __init__(self, trace: dict) -> None:
        trace = _copy(trace)
        if not isinstance(trace, dict) or trace.get("schema_version") not in (1, 2) or not isinstance(trace.get("events"), list):
            raise ValueError("Invalid tool trace")
        for number, event in enumerate(trace["events"], 1):
            checksum = event.get("sha256")
            if (event.get("event_id") != number or event.get("task_id") != trace.get("task_id")
                    or event.get("status") != "completed" or event.get("result") is None
                    or checksum != hashlib.sha256(_json({key: value for key, value in event.items() if key != "sha256"})).hexdigest()):
                raise ValueError("Incomplete or changed tool event; do not automatically retry it")
        self._events, self._position = trace["events"], 0

    def call(self, tool: str, arguments: dict) -> dict:
        if self._position >= len(self._events):
            raise ValueError("No recorded event remains")
        event = self._events[self._position]
        if event["tool"] != tool or _json(event["arguments"]) != _json(arguments):
            raise ValueError("Call does not match the next recorded observation")
        self._position += 1
        return _copy(event["result"])
