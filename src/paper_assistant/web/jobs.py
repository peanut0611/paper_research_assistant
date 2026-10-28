"""Local background research jobs using the existing bounded CLI.

GET requests only inspect snapshots. A new explicit POST generates a fresh task;
refreshing, polling and reconnecting never start or resume model calls.
"""

import json
import os
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from paper_assistant.research.state import AgentState
from paper_assistant.research.toolset import _redact
from paper_assistant.tools.arxiv.fetch import load_search_candidates
from paper_assistant.research.task import ResearchLimits, ResearchTask
from paper_assistant.storage import atomic_write_bytes
from paper_assistant.config import get_openai_api_key, valid_api_key_format
from paper_assistant.research.conversation import ConversationStore


JOB_ID = re.compile(r"web-[0-9]{8}T[0-9]{6}-[a-f0-9]{12}")
ACTIVE = {"queued", "running", "stopping"}


class WebResearchError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read_json(path, limit=16000000):
    if path.stat().st_size > limit:
        raise ValueError("Task snapshot exceeds its size limit")
    return json.loads(path.read_bytes())


def public_state(state):
    checked = next((row["result"]["data"] for row in reversed(state.observations)
                    if row["tool"] == "verify_citations" and row["result"]["success"]), None)
    candidates = [{key: row.get(key) for key in ("arxiv_id", "version", "document_id", "title", "status", "fetch_status",
                   "page_count", "warnings", "last_error")} for row in state.candidates]
    return _redact({"status": state.status, "stop_reason": state.stop_reason, "summary": state.summary,
        "steps": state.steps, "elapsed_seconds": state.elapsed_seconds, "usage": state.usage,
        "tool_limits": state.tool_limits, "candidates": candidates, "selections": state.selections,
        "gaps": state.gaps, "queries": state.queries, "last_error": state.last_error,
        "agent_events": state.agent_events, "message_count": len(state.messages),
        "evidence_answer": checked, "model_events": state.model_events,
        "pending": {"phase": state.pending["phase"], "tool": state.pending.get("decision", {}).get("tool"),
                    "purpose": state.pending.get("decision", {}).get("purpose", "")} if state.pending else None})


class ResearchJobService:
    def __init__(self, project_root: Path, work_lock: threading.Lock, *, execute=None):
        self.project_root = Path(project_root).resolve()
        self.root = self.project_root / "data/research_runs"
        self.seed_path = self.project_root / "data/literature_search/smoke/20261023.json"
        self.work_lock = work_lock
        self._execute = execute or self._run_process
        self._jobs = {}
        self._workers = {}
        self._chat_active = {}
        self._chat_workers = {}
        self._lock = threading.RLock()
        self._closing = False

    def _directory(self, task_id):
        if not isinstance(task_id, str) or JOB_ID.fullmatch(task_id) is None:
            raise WebResearchError("未找到这个页面任务。", 404)
        directory = self.root / task_id
        if not directory.resolve().is_relative_to(self.root.resolve()):
            raise WebResearchError("任务目录无效。", 404)
        return directory

    def status(self):
        try:
            key_ready = valid_api_key_format(get_openai_api_key(self.project_root))
        except ValueError:
            key_ready = False
        cached = False
        try:
            _, rows = load_search_candidates(self.seed_path)
            cached = bool(rows)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        with self._lock:
            active_id = next((task_id for task_id, job in self._jobs.items() if job["status"] in ACTIVE), None)
            active_id = active_id or next(iter(self._chat_active), None)
        return {"key_ready": key_ready, "busy": self.work_lock.locked(), "active_job_id": active_id,
                "cached_example_ready": cached, "model": "gpt-5.4-mini", "max_cost_usd": 0.10,
                "report_reserve_usd": 0.02, "max_runtime_seconds": 300}

    def _validate(self, payload):
        allowed = {"topic", "start_date", "end_date", "questions", "target_papers", "max_cost_usd", "source_mode"}
        required = allowed - {"max_cost_usd"}
        if not isinstance(payload, dict) or set(payload) - allowed or not required <= set(payload):
            raise WebResearchError("请填写主题、UTC 日期范围、研究问题和篇数。")
        if not isinstance(payload["questions"], list):
            raise WebResearchError("研究问题格式无效。")
        mode = payload["source_mode"]
        if mode not in ("online", "cached_example"):
            raise WebResearchError("请选择联网发现或缓存示例。")
        try:
            dates = []
            for key in ("start_date", "end_date"):
                raw = payload[key]
                if not isinstance(raw, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw) is None:
                    raise ValueError("Invalid date")
                dates.append(datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc))
            limits = ResearchLimits(max_model_cost_usd=payload.get("max_cost_usd", .10))
            if limits.max_model_cost_usd > 0.10:
                raise ValueError("Web budget is limited to 0.10 USD")
            task = ResearchTask(payload["topic"], tuple(payload["questions"]), *dates,
                                target_papers=payload["target_papers"], limits=limits)
            if mode == "cached_example":
                _, candidates = load_search_candidates(self.seed_path)
                if not candidates or any(not dates[0] <= row.submitted_at < dates[1] for row in candidates):
                    raise ValueError("Cached candidates do not fit the requested dates")
        except (OSError, ValueError, KeyError, TypeError):
            raise WebResearchError("请检查日期先后、问题长度、1–3 篇目标及大于 $0.02 且不超过 $0.10 的预算；缓存示例需包含其候选提交日期。") from None
        return task

    def start(self, payload):
        task = self._validate(payload)
        try:
            key = get_openai_api_key(self.project_root)
        except ValueError:
            raise WebResearchError("无法读取项目根目录 .env，请检查格式和文件编码。") from None
        if not valid_api_key_format(key):
            raise WebResearchError("请在项目根目录 .env 中填写有效格式的 OPENAI_API_KEY 后刷新页面。")
        with self._lock:
            if self._closing or not self.work_lock.acquire(blocking=False):
                raise WebResearchError("已有研究任务正在处理，请等待完成。", 409)
            task_id = "web-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
            job = {"task_id": task_id, "status": "queued", "created_at": _now(), "finished_at": None,
                   "stop_requested": False, "error": None,
                   "request": json.loads(json.dumps({**payload, "max_cost_usd": task.limits.max_model_cost_usd}))}
            try:
                directory = self._directory(task_id)
                directory.mkdir(parents=True, exist_ok=False)
                self._save_job(job)
                self._jobs[task_id] = job
                worker = threading.Thread(target=self._worker, args=(task_id, task), daemon=True, name="paper-research-" + task_id)
                self._workers[task_id] = worker
                worker.start()
            except Exception:
                self.work_lock.release()
                raise
            return self.snapshot(task_id)

    def _save_job(self, job):
        atomic_write_bytes(self._directory(job["task_id"]) / "web_job.json",
                           json.dumps(_redact(job), ensure_ascii=False, allow_nan=False).encode("utf-8"))

    def _command(self, job, task):
        command = [sys.executable, "-m", "paper_assistant", "research", "--task-id", job["task_id"],
            "--root", str(self.root), "--topic=" + task.topic, "--start", task.start_at.isoformat(), "--end", task.end_at.isoformat(),
            "--target-papers", str(task.target_papers), "--max-cost-usd", str(task.limits.max_model_cost_usd), "--stop-on-request"]
        for question in task.research_questions:
            command += ["--question=" + question]
        if job["request"]["source_mode"] == "cached_example":
            command += ["--seed-search-results", str(self.seed_path), "--offline-sources"]
        return command

    def _run_process(self, command, directory):
        # stdout/stderr stay in an ignored local file, never served as arbitrary diagnostics.
        with (directory / "web_process.log").open("wb") as log:
            with subprocess.Popen(command, cwd=self.project_root, stdout=log, stderr=subprocess.STDOUT,
                                  shell=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0) as child:
                return child.wait()

    def _worker(self, task_id, task):
        try:
            with self._lock:
                job = self._jobs[task_id]
                job["status"] = "stopping" if job["stop_requested"] else "running"
                self._save_job(job)
                command = self._command(job, task)
            code = self._execute(command, self._directory(task_id))
            with self._lock:
                job["status"] = "finished" if code == 0 else "failed"
                if code != 0:
                    job["error"] = "研究未正常完成。请查看下方错误和已取得的结果；没有自动重试。"
        except Exception:
            with self._lock:
                self._jobs[task_id].update(status="failed", error="无法完成后台研究，请检查终端及本地任务文件。没有自动重试。")
        finally:
            with self._lock:
                job = self._jobs[task_id]
                job["finished_at"] = _now()
                try:
                    self._save_job(job)
                finally:
                    self.work_lock.release()

    def _job(self, task_id):
        directory = self._directory(task_id)
        if task_id in self._jobs:
            return dict(self._jobs[task_id])
        try:
            job = _read_json(directory / "web_job.json", 32768)
            if (not isinstance(job, dict) or not {"task_id", "status", "request", "created_at", "stop_requested"} <= job.keys()
                    or job["task_id"] != task_id):
                raise ValueError("Mismatched job")
        except (OSError, ValueError, KeyError, TypeError):
            raise WebResearchError("未找到这个页面任务。", 404) from None
        if job["status"] in ACTIVE:
            job.update(status="interrupted", error="服务已重启，前次执行状态未确认；不会自动重试。已保存的材料仍可查看。")
        return job

    def snapshot(self, task_id):
        with self._lock:
            job = self._job(task_id)
            directory = self._directory(task_id)
            state, events = None, []
            try:
                state = public_state(AgentState.load(directory / "runner_state.json"))
            except FileNotFoundError:
                pass
            except (OSError, ValueError, KeyError, TypeError):
                job["error"] = "当前任务状态无法读取，请保留本地记录并检查文件。"
            try:
                trace = _read_json(directory / "tool_trace.json")
                for row in trace["events"]:
                    result = row.get("result") or {}
                    events.append({"event_id": row["event_id"], "tool": row["tool"], "purpose": row.get("purpose", ""),
                        "status": row["status"], "duration_ms": row.get("duration_ms"), "success": result.get("success"),
                        "error": result.get("error"), "warnings": result.get("warnings", [])})
            except FileNotFoundError:
                pass
            except (OSError, ValueError, KeyError, TypeError):
                job["error"] = "工具记录暂不可读；不会自动重执行调用。"
            if job["status"] == "interrupted" and state and state["status"] == "stopped":
                job["status"] = "failed" if state["last_error"] else "finished"
            return _redact({"job": job, "state": state, "events": events})

    def stop(self, task_id):
        with self._lock:
            job = self._job(task_id)
            if task_id in self._jobs and job["status"] in ACTIVE:
                job = self._jobs[task_id]
                atomic_write_bytes(self._directory(task_id) / "stop.request", b"stop after current step\n")
                job.update(stop_requested=True, status="stopping")
                self._save_job(job)
            return self.snapshot(task_id)

    def close(self):
        with self._lock:
            self._closing = True
            for task_id, job in self._jobs.items():
                if job["status"] in ACTIVE:
                    self.stop(task_id)
            for task_id in list(self._chat_active):
                self.stop_conversation(task_id)

    def conversation(self, task_id):
        with self._lock:
            job = self._job(task_id)
            directory = self._directory(task_id)
            try:
                store = ConversationStore(directory)
                snapshot = store.snapshot(self._chat_active.get(task_id))
                state = AgentState.load(directory / "runner_state.json")
                manifest = _read_json(directory / "workspace.json")
                ready = (job["status"] not in ACTIVE and state.status == "stopped"
                         and bool(manifest.get("active_index")))
                snapshot.update(available=ready, reason="" if ready else "请先完成研究并成功入库至少一篇论文。")
                return snapshot
            except (OSError, ValueError, KeyError, TypeError):
                raise WebResearchError("当前任务还没有可用的对话资料，请先完成研究并成功入库。", 409) from None

    def ask(self, task_id, payload):
        if isinstance(payload, dict):
            payload = {"max_cost_usd": .05, **payload}
        with self._lock:
            self._job(task_id)
            store = ConversationStore(self._directory(task_id))
            try:
                store.validate(payload)
                if store.existing(payload):
                    return self.conversation(task_id)
            except ValueError as error:
                raise WebResearchError(str(error)) from None
            if not self.conversation(task_id)["available"]:
                raise WebResearchError("请先完成研究并成功入库，之后才能继续讨论。", 409)
            if not valid_api_key_format(get_openai_api_key(self.project_root)):
                raise WebResearchError("请先在 .env 配置 OPENAI_API_KEY。")
            if self._closing or not self.work_lock.acquire(blocking=False):
                raise WebResearchError("已有研究或追问正在执行，请等待完成。", 409)
            try:
                turn = store.begin(payload)
                self._chat_active[task_id] = turn["id"]
                worker = threading.Thread(target=self._chat_worker, args=(task_id, turn["id"]), daemon=True,
                                          name="paper-discussion-" + turn["id"])
                self._chat_workers[turn["id"]] = worker
                worker.start()
            except ValueError as error:
                self._chat_active.pop(task_id, None)
                self.work_lock.release()
                raise WebResearchError(str(error)) from None
            except Exception:
                self._chat_active.pop(task_id, None)
                self.work_lock.release()
                raise
            return self.conversation(task_id)

    def _chat_worker(self, task_id, identifier):
        store = ConversationStore(self._directory(task_id))
        try:
            with self._lock:
                store.update(identifier, status="running")
            command = [sys.executable, "-m", "paper_assistant", "chat", "--task-id", task_id,
                       "--turn-id", identifier, "--root", str(self.root)]
            code = self._execute(command, store.turn_directory(identifier))
            with self._lock:
                store.update(identifier, status="finished" if code == 0 else "failed", finished_at=_now(),
                             error=None if code == 0 else "本轮未正常完成，请查看停止原因；不会自动重试。")
        except Exception:
            with self._lock:
                store.update(identifier, status="failed", error="无法完成本轮讨论；不会自动重试。", finished_at=_now())
        finally:
            with self._lock:
                self._chat_active.pop(task_id, None)
                self.work_lock.release()

    def stop_conversation(self, task_id):
        with self._lock:
            self._job(task_id)
            identifier = self._chat_active.get(task_id)
            if identifier:
                store = ConversationStore(self._directory(task_id))
                atomic_write_bytes(store.turn_directory(identifier) / "stop.request", b"stop after current step\n")
                store.update(identifier, status="stopping")
            return self.conversation(task_id)

    def manage_conversation(self, task_id, action, payload):
        with self._lock:
            job = self._job(task_id)
            if job["status"] in ACTIVE or self._closing or not self.work_lock.acquire(blocking=False):
                raise WebResearchError("请先等待当前研究或追问结束，再新建、重置或切换对话。", 409)
            try:
                self.conversation(task_id)
                ConversationStore(self._directory(task_id)).manage(action, payload)
                return self.conversation(task_id)
            except ValueError as error:
                raise WebResearchError(str(error)) from None
            finally:
                self.work_lock.release()
