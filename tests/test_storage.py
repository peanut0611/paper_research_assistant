from pathlib import Path

import pytest

from paper_assistant import storage


def locked(code):
    error = PermissionError("Injected Windows file lock")
    error.winerror = code
    return error


@pytest.mark.parametrize("code", [5, 32, 33])
def test_temporary_windows_lock_preserves_old_record_until_complete_publication(tmp_path, monkeypatch, code):
    target = tmp_path / "record.json"
    target.write_bytes(b"old complete record")
    original = Path.replace
    attempts, delays = [], []
    monkeypatch.setattr(storage, "_WINDOWS", True)
    monkeypatch.setattr(storage.time, "sleep", delays.append)
    def replace(source, destination):
        attempts.append(source)
        if len(attempts) <= 2:
            assert target.read_bytes() == b"old complete record"
            raise locked(code)
        return original(source, destination)
    monkeypatch.setattr(Path, "replace", replace)
    storage.atomic_write_bytes(target, b"new complete record")
    assert target.read_bytes() == b"new complete record"
    assert len(attempts) == 3 and len(delays) == 2
    assert list(tmp_path.iterdir()) == [target]


def test_persistent_windows_denial_is_bounded_and_keeps_original_record(tmp_path, monkeypatch):
    target = tmp_path / "record.json"
    target.write_bytes(b"original")
    attempts, delays = [], []
    monkeypatch.setattr(storage, "_WINDOWS", True)
    monkeypatch.setattr(storage.time, "sleep", delays.append)
    def replace(source, destination):
        attempts.append(source)
        raise locked(5)
    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(PermissionError):
        storage.atomic_write_bytes(target, b"not published")
    assert 1 < len(attempts) <= 6 and sum(delays) < 1
    assert target.read_bytes() == b"original" and list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("windows,code", [(False, 5), (True, 183)])
def test_other_permission_errors_fail_without_retry(tmp_path, monkeypatch, windows, code):
    source = tmp_path / "source"
    source.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(storage, "_WINDOWS", windows)
    def no_sleep(delay):
        pytest.fail("Unrelated permission errors must not be retried")
    monkeypatch.setattr(storage.time, "sleep", no_sleep)
    def replace(source, destination):
        raise locked(code)
    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(PermissionError):
        storage.replace_path(source, tmp_path / "destination")
    assert source.read_text(encoding="utf-8") == "keep"


def test_staged_directory_can_be_published_after_a_transient_lock(tmp_path, monkeypatch):
    stage, destination = tmp_path / "stage", tmp_path / "published"
    stage.mkdir()
    (stage / "manifest.json").write_text("complete", encoding="utf-8")
    original = Path.replace
    calls = []
    monkeypatch.setattr(storage, "_WINDOWS", True)
    monkeypatch.setattr(storage.time, "sleep", lambda delay: None)
    def replace(source, target):
        calls.append(source)
        if len(calls) == 1:
            assert not destination.exists() and (stage / "manifest.json").exists()
            raise locked(5)
        return original(source, target)
    monkeypatch.setattr(Path, "replace", replace)
    storage.replace_path(stage, destination)
    assert not stage.exists() and (destination / "manifest.json").read_text(encoding="utf-8") == "complete"
