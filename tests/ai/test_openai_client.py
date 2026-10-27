from types import SimpleNamespace

import pytest

from paper_assistant.ai.openai import create_openai_client, describe_api_error


@pytest.mark.parametrize("value", ["", "中文密钥", "sk-test internal-space"])
def test_invalid_local_key_is_rejected_before_sdk_request(monkeypatch, value):
    monkeypatch.setenv("OPENAI_API_KEY", value)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        create_openai_client()


def error(body, content_type="application/json"):
    return SimpleNamespace(request=SimpleNamespace(url=SimpleNamespace(path="/v1/responses")),
        body=body, code=None, request_id="req-test", status_code=400,
        response=SimpleNamespace(headers={"content-type": content_type}, content=b"response"))


def test_diagnostics_include_stage_and_parameter_but_redact_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "local-secret-example")
    diagnostic = describe_api_error(error({"error": {"message": "local-secret-example Bearer hidden sk-test-secret", "param": "tools"}}))
    assert "stage=research_decision" in diagnostic and "param: tools" in diagnostic
    assert all(secret not in diagnostic for secret in ("local-secret-example", "hidden", "sk-test-secret"))


def test_unstructured_body_is_never_dumped():
    diagnostic = describe_api_error(error("<html>private gateway content</html>", "text/html"))
    assert "No structured API error" in diagnostic
    assert "private gateway" not in diagnostic
