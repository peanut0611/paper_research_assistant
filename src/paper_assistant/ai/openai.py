"""Official OpenAI client and redacted diagnostics for research decisions."""
import re
from typing import Any

from paper_assistant.config import get_openai_api_key, valid_api_key_format

MODEL_NAME = "gpt-5.4-mini"
OPENAI_BASE_URL = "https://api.openai.com/v1"

def create_openai_client(*, timeout: float = 60, trust_env: bool = True) -> Any:
    """Create the official client for bounded research decisions."""
    api_key = get_openai_api_key()
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not configured; fill in project .env, never paste it into chat")
    if not valid_api_key_format(api_key):
        raise ValueError("OPENAI_API_KEY contains non-ASCII characters or internal whitespace; re-enter it locally")
    from openai import DefaultHttpxClient, OpenAI
    return OpenAI(api_key=api_key, base_url=OPENAI_BASE_URL, timeout=timeout, max_retries=0,
                  http_client=DefaultHttpxClient(trust_env=trust_env))


def describe_api_error(error: Any) -> str:
    """Expose useful API diagnostics without dumping requests or credentials."""
    def redact(value: object) -> str:
        text = str(value)
        try:
            secret = get_openai_api_key()
        except ValueError:
            secret = ""
        if secret:
            text = text.replace(secret, "[REDACTED]")
        text = re.sub(r"(?i)\bsk-[a-z0-9_.*-]+", "[REDACTED]", text)
        text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", text)
        return re.sub(r"[\x00-\x1f\x7f]", " ", text)[:600]

    path = error.request.url.path
    stage = ("research_decision" if path.endswith("/responses") else
        "model_lookup" if "/models/" in path else "unknown")
    body = error.body
    detail = body.get("error", body) if isinstance(body, dict) else None
    if not isinstance(detail, dict):
        detail = {}
    message = detail.get("message")
    content_type = error.response.headers.get("content-type", "unknown")
    lines = [f"OpenAI HTTP {error.status_code}; stage={stage}; code={redact(error.code or detail.get('code'))}; request_id={redact(error.request_id)}."]
    if isinstance(message, str) and message.strip():
        lines.append(f"message: {redact(message)}")
    else:
        lines.append(f"No structured API error message; content_type={redact(content_type)}. The response may have come from a gateway/proxy; origin is unconfirmed.")
    if detail.get("param") is not None:
        lines.append(f"param: {redact(detail['param'])}")
    lines.append(f"response_body_bytes={len(error.response.content)}")
    return "\n".join(lines)
