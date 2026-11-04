"""Collect research baselines through the local HTTP interface, without changing the agent."""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from http.client import HTTPConnection, HTTPException
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit
import uuid

from paper_assistant.storage import atomic_write_bytes


def capture_case(case, request, *, mode, execute=False, evidence_kind="real_model_http",
                 poll_interval=1, timeout_seconds=420, checkpoint=lambda result: None,
                 material_errors=(), execution_blockers=()):
    """Keep execution evidence and human scoring separate; never submit the rubric."""
    if mode not in ("online", "fixed"):
        raise ValueError("mode must be online or fixed")
    result = {
        "case_id": case["id"], "started_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode, "evidence_kind": evidence_kind, "execution_status": "not_executed",
        "input": {**case["web_payload"], "source_mode": "online" if mode == "online" else "cached_example"},
        "blockers": list(execution_blockers) + (list(material_errors) if mode == "fixed" else []),
        "research": None, "followups": [], "http_log": [],
        "review": {"verdict": "not_reviewed", "claims": [], "failure_reasons": []},
    }
    transport = request
    def request(method, path, payload=None):
        try:
            status, body = transport(method, path, payload)
        except (OSError, ValueError, HTTPException):
            status, body = -1, {"error": "transport_failure_outcome_unknown_do_not_replay"}
        result["http_log"].append({"method": method, "path": path, "status": status})
        return status, body
    status, readiness = request("GET", "/api/research/status")
    result["readiness"] = readiness
    if status != 200:
        result["blockers"].append("status_endpoint_unavailable")
    elif not readiness.get("key_ready"):
        result["blockers"].append("api_key_not_ready")
    if readiness.get("busy"):
        result["blockers"].append("server_busy")
    if mode == "fixed" and not readiness.get("cached_example_ready"):
        result["blockers"].append("fixed_seed_not_ready")
    if not execute:
        result["blockers"].append("execution_not_requested")
    if result["blockers"]:
        return result
    result["execution_status"] = "submitting_research"
    checkpoint(result)
    status, snapshot = request("POST", "/api/research/jobs", result["input"])
    result["research"] = snapshot
    if status != 202:
        result["execution_status"] = "submission_unknown" if status == -1 else "http_rejected"
        checkpoint(result)
        return result
    job_id = snapshot["job"]["task_id"]
    result["job_id"] = job_id
    result["execution_status"] = "collecting_research"
    checkpoint(result)
    deadline = time.monotonic() + timeout_seconds
    while snapshot["job"]["status"] in ("queued", "running", "stopping"):
        if time.monotonic() >= deadline:
            request("POST", f"/api/research/jobs/{job_id}/stop", {})
            result["execution_status"] = "collector_timeout"
            return result
        time.sleep(poll_interval)
        status, received = request("GET", f"/api/research/jobs/{job_id}")
        if status != 200:
            result["execution_status"] = "collection_failed"
            result["collection_error"] = received
            checkpoint(result)
            return result
        snapshot = received
        result["research"] = snapshot
    for question in case.get("followups", []):
        route = f"/api/research/jobs/{job_id}/conversation"
        row = {"input": {"question": question, "request_id": "baseline-" + uuid.uuid4().hex,
                         "max_cost_usd": .05}, "snapshot": None}
        result["followups"].append(row)
        result["execution_status"] = "submitting_followup"
        checkpoint(result)
        status, conversation = request("POST", route, row["input"])
        row["snapshot"] = conversation
        if status != 202:
            result["execution_status"] = "submission_unknown" if status == -1 else "followup_rejected"
            checkpoint(result)
            return result
        result["execution_status"] = "collecting_followup"
        checkpoint(result)
        deadline = time.monotonic() + timeout_seconds
        while conversation.get("active_turn_id"):
            if time.monotonic() >= deadline:
                request("POST", route + "/stop", {})
                result["execution_status"] = "collector_timeout"
                checkpoint(result)
                return result
            time.sleep(poll_interval)
            status, received = request("GET", route)
            if status != 200:
                result["execution_status"] = "collection_failed"
                row["collection_error"] = received
                checkpoint(result)
                return result
            conversation = received
            row["snapshot"] = conversation
    result["execution_status"] = "executed"
    checkpoint(result)
    return result


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_json(path, value):
    atomic_write_bytes(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode() + b"\n")


def freeze_workspace(root):
    """Fingerprint allowlisted source/configuration, including untracked local work."""
    scopes = ("src", "tests", "scripts", "eval_data", "docs/agents", "docs/adr")
    names = ("pyproject.toml", "uv.lock", "README.md", "AGENTS.md", "GLOSSARY.md", ".gitignore",
             ".env.example", ".agents/mattpocock-skills-source.json")
    files = {root / name for name in names if (root / name).is_file()}
    for scope in scopes:
        for path in (root / scope).rglob("*"):
            relative = path.relative_to(root)
            if (path.is_file() and not any(part.startswith(".") or part == "__pycache__"
                                          for part in relative.parts)
                    and path.suffix.lower() in (".py", ".cjs", ".js", ".html", ".css", ".md", ".json", ".jsonl", ".xml", ".toml")):
                files.add(path)
    files = {path for path in files if not path.is_symlink() and path.resolve().is_relative_to(root)}
    hashes = {path.relative_to(root).as_posix(): file_hash(path) for path in sorted(files)}
    selected = [*scopes, *names]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    diff = subprocess.check_output(["git", "diff", "HEAD", "--", *selected], cwd=root)
    tracked = set(subprocess.check_output(["git", "ls-files", "-z", "--", *selected], cwd=root).decode().split("\0"))
    return {"head": head, "files": hashes,
            "untracked_files": sorted(set(hashes) - tracked),
            "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
            "workspace_sha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
            "exclusions": ["credentials", "virtual environments", "data and model caches", ".git", "third-party skill bodies"]}


def local_json_request(base_url):
    """Only the existing loopback service is a valid evaluation target."""
    target = urlsplit(base_url)
    if (target.scheme != "http" or target.hostname not in ("127.0.0.1", "localhost")
            or target.username or target.password or target.path not in ("", "/") or target.query or target.fragment):
        raise ValueError("Evaluation requires an http://127.0.0.1:PORT loopback service")
    def request(method, path, payload=None):
        connection = HTTPConnection(target.hostname, target.port or 80, timeout=15)
        try:
            body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
            connection.request(method, path, body=body, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read(16_000_001)
            if len(raw) > 16_000_000:
                raise ValueError("HTTP response exceeds the capture limit")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("Expected an HTTP JSON object")
            return response.status, value
        finally:
            connection.close()
    return request


def material_errors(book, case, root):
    """Validate provenance and local bytes, without downloading or modifying a seed."""
    from paper_assistant.tools.arxiv.fetch import PaperFetcher, PaperFetchError, load_search_candidates
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    errors, candidates = [], {}
    seed = root / book["fixed_seed"]["path"]
    if not seed.resolve().is_relative_to(root) or not seed.is_file():
        errors.append("fixed_seed_missing")
    elif file_hash(seed) != book["fixed_seed"].get("sha256"):
        errors.append("fixed_seed_checksum_unverified")
    else:
        try:
            _, rows = load_search_candidates(seed)
            candidates = {(row.arxiv_id, row.version): row for row in rows}
        except (OSError, ValueError):
            errors.append("fixed_seed_not_live_arxiv_report")
    for identifier in case["source_ids"]:
        source = book["sources"][identifier]
        candidate = candidates.get((source["arxiv_id"], source["version"]))
        if candidate is None:
            errors.append(identifier + ":pinned_candidate_missing")
        path = root / (source.get("pdf_path") or "__missing_pdf__")
        if not path.resolve().is_relative_to(root) or not path.is_file():
            errors.append(identifier + ":pdf_missing")
            continue
        if file_hash(path) != source.get("pdf_sha256"):
            errors.append(identifier + ":pdf_checksum_unverified")
            continue
        try:
            asset = PaperFetcher(cache_directory=root / "data/paper_cache").cached_paper(candidate) if candidate else None
            if not asset or asset.origin != "live_arxiv_pdf" or asset.source_sha256 != source["pdf_sha256"]:
                errors.append(identifier + ":application_cache_not_verified_live_pdf")
            pdf = PdfReader(path)
            if len(pdf.pages) != source.get("page_count") or not source.get("evidence"):
                errors.append(identifier + ":page_evidence_missing")
            for evidence in source.get("evidence", []):
                page = pdf.pages[evidence["page_number"] - 1].extract_text() or ""
                quote = evidence["quote"]
                if (not quote or not evidence.get("claim") or not evidence.get("reviewer")
                        or evidence["page_number"] < 1 or "".join(quote.split()) not in "".join(page.split())):
                    errors.append(identifier + ":page_evidence_unverified")
        except (OSError, ValueError, KeyError, IndexError, PaperFetchError, PdfReadError):
            errors.append(identifier + ":pdf_or_cache_validation_failed")
    return errors


def network_blockers(mode):
    """Report the managed policy; do not probe or bypass denied destinations."""
    path = Path("/etc/codex/network-policy.json")
    if not path.exists():
        return []
    policy = json.loads(path.read_bytes()).get("http_network_policy", {})
    if policy.get("type") != "restricted":
        return []
    allowed = {rule.get("host") for rule in policy.get("egress_rules", [])}
    required = ["api.openai.com"] + (["export.arxiv.org", "arxiv.org"] if mode == "online" else [])
    return ["network_policy_blocks:" + host for host in required if host not in allowed]


def collect_local_artifacts(result, root):
    """Export the known run's full tool trace and policy binding, not arbitrary files."""
    from paper_assistant.research.state import AgentState
    from paper_assistant.research.toolset import _redact
    snapshot = result.get("research") or {}
    job_id = snapshot.get("job", {}).get("task_id", "")
    if re.fullmatch(r"web-\d{8}T\d{6}-[a-f0-9]{12}", job_id) is None:
        return {}
    directory = root / "data/research_runs" / job_id
    if not directory.resolve().is_relative_to((root / "data/research_runs").resolve()):
        return {}
    artifacts = {}
    run_directories = [("research", directory)]
    for row in result["followups"]:
        for turn in (row.get("snapshot") or {}).get("turns", []):
            identifier = turn.get("id", "")
            if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identifier):
                run_directories.append((identifier, directory / "conversation" / identifier))
    for label, run in run_directories:
        if not run.resolve().is_relative_to(directory.resolve()):
            continue
        item = {}
        try:
            state = AgentState.load(run / "runner_state.json")
            item["binding"] = state.binding
            item["model_events"] = state.model_events
        except (OSError, ValueError, KeyError, TypeError):
            item["binding_status"] = "not_available"
        trace = run / "tool_trace.json"
        if trace.is_file() and trace.stat().st_size <= 16_000_000:
            item["tool_trace"] = json.loads(trace.read_bytes())
        artifacts[label] = item
    return _redact(artifacts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "run"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--suite", type=Path, default=Path("eval_data/agent_v1/suite.json"))
    parser.add_argument("--sources", type=Path, default=Path("eval_data/agent_v1/sources.json"))
    parser.add_argument("--frozen", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mode", choices=("online", "fixed"), default="fixed")
    parser.add_argument("--base-url", default="http://127.0.0.1:8501")
    parser.add_argument("--case", dest="case_id")
    parser.add_argument("--execute", action="store_true", help="Submit paid model work; omitted means readiness checks only")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.command == "freeze":
            suite_path, sources_path = root / args.suite, root / args.sources
            suite = json.loads(suite_path.read_bytes())
            sources = json.loads(sources_path.read_bytes())
            rubric = (suite_path.parent / suite["rubric"]).read_bytes()
            workspace = freeze_workspace(root)
            args.out.mkdir(parents=True, exist_ok=False)
            save_json(args.out / "suite.json", suite)
            save_json(args.out / "sources.json", sources)
            (args.out / "rubric.md").write_bytes(rubric)
            save_json(args.out / "workspace.json", workspace)
            save_json(args.out / "freeze.json", {"frozen_at": datetime.now(timezone.utc).isoformat(),
                "sha256": {name: file_hash(args.out / name) for name in ("suite.json", "sources.json", "rubric.md", "workspace.json")}})
            print("Frozen local inputs, rubric and workspace fingerprint:", args.out)
            return
        if not args.frozen:
            parser.error("run requires --frozen")
        frozen = args.frozen.resolve()
        lock = json.loads((frozen / "freeze.json").read_bytes())
        for name in ("suite.json", "sources.json", "rubric.md", "workspace.json"):
            if file_hash(frozen / name) != lock["sha256"][name]:
                raise ValueError("Frozen inputs changed; create a new revision before running")
        suite = json.loads((frozen / "suite.json").read_bytes())
        sources = json.loads((frozen / "sources.json").read_bytes())
        cases = [case for case in suite["cases"] if args.case_id is None or case["id"] == args.case_id]
        if not cases or any(re.fullmatch(r"[a-z0-9-]+", case["id"]) is None for case in cases):
            raise ValueError("Unknown or invalid case ID")
        client = local_json_request(args.base_url)
        from paper_assistant.research.model import OpenAIModelConfig, INSTRUCTIONS, RATES, PRICE_CHECKED_AT
        args.out.mkdir(parents=True, exist_ok=False)
        report = {"schema_version": 1, "suite_id": suite["id"], "frozen_inputs": lock,
            "workspace": freeze_workspace(root), "mode": args.mode,
            "model_config": asdict(OpenAIModelConfig()), "rates": RATES, "price_checked_at": PRICE_CHECKED_AT,
            "research_instructions_sha256": hashlib.sha256(INSTRUCTIONS.encode()).hexdigest(),
            "generation_note": "Current provider uses reasoning effort none; binding/model events record actual policy. No quality score is inferred.",
            "cases": []}
        save_json(args.out / "report.json", report)
        for case in cases:
            start = time.monotonic()
            result = capture_case(case, client, mode=args.mode, execute=args.execute,
                material_errors=material_errors(sources, case, root) if args.mode == "fixed" else (),
                execution_blockers=network_blockers(args.mode),
                checkpoint=lambda value: save_json(args.out / (case["id"] + ".json"), value))
            result["collection_elapsed_seconds"] = round(time.monotonic() - start, 6)
            result["finished_at"] = datetime.now(timezone.utc).isoformat()
            result["local_artifacts"] = collect_local_artifacts(result, root)
            save_json(args.out / (case["id"] + ".json"), result)
            report["cases"].append(result)
            save_json(args.out / "report.json", report)
            print(case["id"], result["execution_status"], "; ".join(result["blockers"]))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Baseline capture stopped ({type(error).__name__}); inspect the existing output before any new submission.\n")


if __name__ == "__main__":
    main()
