import threading

import pytest

from paper_assistant import config
from paper_assistant.ai.openai import create_openai_client, describe_api_error
from paper_assistant.web.jobs import ResearchJobService
from tests.ai.test_openai_client import error


@pytest.fixture
def local_config(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    return tmp_path / ".env"


@pytest.mark.parametrize("entry", [
    "OPENAI_API_KEY=sk-local-env-test",
    'OPENAI_API_KEY = "sk-local-env-test" # comment',
    "export OPENAI_API_KEY='sk-local-env-test'",
    "OPENAI_API_KEY=sk-local-env-test # comment",
    "# OPENAI_API_KEY=ignored\nOTHER=value\nOPENAI_API_KEY=sk-local-env-test",
])
def test_root_env_parsing_works_from_other_directory(local_config, tmp_path, monkeypatch, entry):
    local_config.write_text(entry, encoding="utf-8-sig")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert config.get_openai_api_key() == "sk-local-env-test"


def test_environment_override_and_explicit_empty_value(local_config, monkeypatch):
    local_config.write_text("OPENAI_API_KEY=sk-file-test", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-environment-test")
    assert config.get_openai_api_key() == "sk-environment-test"
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert config.get_openai_api_key() == ""


def test_missing_or_blank_file_returns_no_key(local_config):
    assert config.get_openai_api_key() == ""
    local_config.write_text("OPENAI_API_KEY= # fill locally", encoding="utf-8")
    assert config.get_openai_api_key() == ""


@pytest.mark.parametrize("entry", ['OPENAI_API_KEY="private-unclosed',
    "OPENAI_API_KEY='private' invalid-tail"])
def test_invalid_quotes_fail_without_echoing_secret(local_config, entry):
    local_config.write_text(entry, encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        config.get_openai_api_key()
    assert "private" not in str(caught.value)


def test_client_and_redaction_use_root_env_without_network(local_config):
    local_config.write_text("OPENAI_API_KEY=local-file-secret", encoding="utf-8")
    client = create_openai_client(trust_env=False)
    try:
        assert client.api_key == "local-file-secret"
        assert "local-file-secret" not in describe_api_error(error({"message": "local-file-secret"}))
    finally:
        client.close()


def test_web_readiness_reloads_file_without_exposing_key(local_config, tmp_path):
    service = ResearchJobService(tmp_path, threading.Lock())
    assert not service.status()["key_ready"]
    local_config.write_text("OPENAI_API_KEY=sk-web-file-secret", encoding="utf-8")
    assert service.status()["key_ready"]
    assert "sk-web-file-secret" not in str(service.status())
    local_config.write_text('OPENAI_API_KEY="bad-quotes', encoding="utf-8")
    assert not service.status()["key_ready"]
