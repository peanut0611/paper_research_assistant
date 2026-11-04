"""Deletion behavior at the existing local HTTP boundary, without paid calls."""
import json
from types import SimpleNamespace

from paper_assistant.web.jobs import ResearchJobService
from tests.web.test_research_web import jobs, payload, wait_finished
from tests.web.test_web_app import send_request
import threading


def http(service, task_id, body=None, suffix="/delete", method="POST"):
    server = SimpleNamespace(research=service, server_address=("127.0.0.1", 8501))
    return send_request(server, f"/api/research/jobs/{task_id}{suffix}", payload=body or {}, method=method)


def test_confirmed_deletion_removes_only_target_and_rejects_expired_posts_after_restart(jobs):
    service, started, release, calls = jobs
    request = payload(request_id="research-delete-000001")
    first = service.start(request)["job"]["task_id"]
    assert started.wait(2)
    release.set()
    wait_finished(service, first)
    second = service.start(payload(topic="Other question"))["job"]["task_id"]
    wait_finished(service, second)
    other = service.snapshot(second)
    directory = service.root / first
    (directory / "private-answer.txt").write_text("private research body")
    shared = service.project_root / "data/pdf_cache/shared.pdf"
    shared.parent.mkdir(parents=True)
    shared.write_bytes(b"shared")
    assert http(service, first)[0] == 400
    assert directory.exists()
    assert http(service, first, {"confirmed": True, "path": str(shared)})[0] == 400
    assert http(service, first, {"confirmed": True})[0] == 200
    assert not directory.exists() and shared.read_bytes() == b"shared"
    assert service.snapshot(second) == other
    restarted = ResearchJobService(service.project_root, threading.Lock(), execute=lambda *a: 99)
    assert [row["task_id"] for row in restarted.dialogues()["dialogues"]] == [second]
    assert http(restarted, first, suffix="", method="GET")[0] == 410
    assert http(restarted, first, {"confirmed": True})[0] == 410
    assert http(restarted, first, {"question": "late"}, suffix="/conversation")[0] == 410
    server = SimpleNamespace(research=restarted, server_address=("127.0.0.1", 8501))
    assert send_request(server, payload=request)[0] == 410
    assert send_request(server, payload={**request, "topic": "Changed"})[0] == 410
    assert len(calls) == 2 and not directory.exists()
    tombstones = (service.root / ".deleted").read_text()
    assert first in tombstones and "private" not in tombstones and request["topic"] not in tombstones


def test_running_research_must_finish_after_stop_before_deletion(jobs):
    service, started, release, calls = jobs
    task_id = service.start(payload())["job"]["task_id"]
    assert started.wait(2)
    assert http(service, task_id, {"confirmed": True})[0] == 409
    assert http(service, task_id, suffix="/stop")[0] == 200
    assert http(service, task_id, {"confirmed": True})[0] == 409
    assert (service.root / task_id).exists()
    release.set()
    wait_finished(service, task_id)
    assert http(service, task_id, {"confirmed": True})[0] == 200
    assert not (service.root / task_id).exists() and len(calls) == 1


def test_delete_rejects_foreign_ids_and_symlinked_dialogue_or_root(jobs, tmp_path):
    service, _, release, _ = jobs
    release.set()
    target = service.start(payload())["job"]["task_id"]
    wait_finished(service, target)
    other = service.start(payload(topic="Keep this"))["job"]["task_id"]
    wait_finished(service, other)
    for identifier in ("../", target + "/../" + other, "%2e%2e", "not-a-web-job"):
        assert http(service, identifier, {"confirmed": True})[0] == 404
    alias = "web-20261027T000000-bbbbbbbbbbbb"
    (service.root / alias).symlink_to(service.root / other, target_is_directory=True)
    assert http(service, alias, {"confirmed": True})[0] == 404
    # Links inside owned data are removed as links, never traversed into shared data.
    (service.root / target / "shared-link").symlink_to(service.root / other, target_is_directory=True)
    assert http(service, target, {"confirmed": True})[0] == 200
    assert service.snapshot(other)["job"]["request"]["topic"] == "Keep this"
    real_root = service.root
    moved = real_root.with_name("elsewhere")
    real_root.rename(moved)
    real_root.symlink_to(moved, target_is_directory=True)
    assert http(service, other, {"confirmed": True})[0] == 404
    assert (moved / other / "web_job.json").is_file()


def test_restart_cannot_hide_a_still_running_research_worker(jobs):
    service, started, release, calls = jobs
    task_id = service.start(payload())["job"]["task_id"]
    assert started.wait(2)
    restarted = ResearchJobService(service.project_root, threading.Lock(), execute=lambda *a: 99)
    assert http(restarted, task_id, {"confirmed": True})[0] == 409
    assert (service.root / task_id).exists()
    release.set()
    wait_finished(service, task_id)
    assert http(restarted, task_id, {"confirmed": True})[0] == 200
    assert len(calls) == 1 and not (service.root / task_id).exists()
