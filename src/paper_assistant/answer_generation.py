"""GPT-5.4 Mini evidence-grounded answers through the OpenAI Responses API."""

import json
import math
import os
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

from paper_assistant.context_builder import ContextBundle, EvidenceSource, PromptMessage, build_context
from paper_assistant.embedding import SearchResult
from paper_assistant.evaluation import normalize_evidence


MODEL_NAME = "gpt-5.4-mini"
OPENAI_BASE_URL = "https://api.openai.com/v1"
# Standard text rates checked against official model documentation, 2026-10-18.
INPUT_USD_PER_MILLION = 0.75
CACHED_INPUT_USD_PER_MILLION = 0.075
OUTPUT_USD_PER_MILLION = 4.50
CONTEXT_WINDOW = 400_000
GENERATION_INSTRUCTIONS = (
    "用问题的语言简洁回答，按指定 JSON schema 输出。answer 中每个有证据支持的关键结论"
    "使用独立的 [E1] 格式引用，不要将多个编号写在同一个方括号内。"
    "citations 列出所用 evidence_id 及对应资料中的简短连续原文 quote，不要改写引文。"
    "insufficient_evidence 表示证据不能完整回答问题；不足时不要使用常识或资料外知识补全，"
    "可回答有证据的部分并说明缺失信息。完全无法回答时 citations 为空，不要编造引用。"
)
ANSWER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "answer": {"type": "string"},
        "insufficient_evidence": {"type": "boolean"},
        "citations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"evidence_id": {"type": "string"}, "quote": {"type": "string"}},
            "required": ["evidence_id", "quote"],
        }},
    },
    "required": ["answer", "insufficient_evidence", "citations"],
}


def create_openai_client(*, timeout: float = 60, trust_env: bool = True) -> Any:
    """Use the same official endpoint and transport for diagnostics/generation."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not configured; set it locally, never paste it into chat")
    if not api_key.isascii() or any(character.isspace() for character in api_key):
        raise ValueError("OPENAI_API_KEY contains non-ASCII characters or internal whitespace; re-enter it locally")
    from openai import DefaultHttpxClient, OpenAI
    return OpenAI(api_key=api_key, base_url=OPENAI_BASE_URL, timeout=timeout, max_retries=0,
                  http_client=DefaultHttpxClient(trust_env=trust_env))


def describe_api_error(error: Any) -> str:
    """Expose useful API diagnostics without dumping requests or credentials."""
    def redact(value: object) -> str:
        text = str(value)
        secret = os.environ.get("OPENAI_API_KEY", "").strip()
        if secret:
            text = text.replace(secret, "[REDACTED]")
        text = re.sub(r"(?i)\bsk-[a-z0-9_.*-]+", "[REDACTED]", text)
        text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", text)
        return re.sub(r"[\x00-\x1f\x7f]", " ", text)[:600]

    path = error.request.url.path
    stage = "token_count" if path.endswith("/responses/input_tokens") else (
        "answer_generation" if path.endswith("/responses") else
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


@dataclass(frozen=True)
class ValidatedCitation:
    evidence_id: str
    quote: str
    sources: tuple[EvidenceSource, ...]


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int

    @property
    def estimated_cost_usd(self) -> float:
        return ((self.input_tokens - self.cached_input_tokens) * INPUT_USD_PER_MILLION
                + self.cached_input_tokens * CACHED_INPUT_USD_PER_MILLION
                + self.output_tokens * OUTPUT_USD_PER_MILLION) / 1_000_000


@dataclass(frozen=True)
class GeneratedAnswer:
    status: Literal["answered", "insufficient_evidence", "no_evidence", "model_refusal", "incomplete", "invalid_response"]
    answer: str | None
    citations: tuple[ValidatedCitation, ...]
    insufficient_evidence: bool | None
    model: str
    response_id: str | None
    usage: TokenUsage | None
    raw_output: str
    error: str | None = None

    def to_dict(self) -> dict:
        return {**asdict(self), "estimated_cost_usd": self.usage.estimated_cost_usd if self.usage else None,
                "citation_validation": "ID existence, inline/list agreement, and quote containment only; semantic entailment not verified"}


@dataclass(frozen=True)
class AnswerRun:
    context: ContextBundle
    result: GeneratedAnswer
    max_output_tokens: int
    maximum_generation_cost_usd: float
    max_cost_usd: float

    def to_dict(self) -> dict:
        return {"context": self.context.to_dict(), "result": self.result.to_dict(),
                "settings": {"requested_model": MODEL_NAME, "reasoning_effort": "none",
                             "generation_instructions": GENERATION_INSTRUCTIONS,
                             "response_schema": ANSWER_SCHEMA,
                             "store": False, "service_tier": "default",
                             "max_output_tokens": self.max_output_tokens,
                             "maximum_generation_cost_usd": self.maximum_generation_cost_usd,
                             "max_cost_usd": self.max_cost_usd,
                             "input_count_is_estimate": self.context.token_counter_name.startswith("Local tiktoken"),
                             "cost_rates_usd_per_million": {"input": INPUT_USD_PER_MILLION,
                                                            "cached_input": CACHED_INPUT_USD_PER_MILLION,
                                                            "output": OUTPUT_USD_PER_MILLION},
                             "cost_rate_date": "2026-10-18", "automatic_retries": 0}}


def _validate_generation_limits(max_output_tokens: int, max_cost_usd: float) -> None:
    if type(max_output_tokens) is not int or not 16 <= max_output_tokens <= 128_000:
        raise ValueError("max_output_tokens must be between 16 and 128000")
    if not math.isfinite(max_cost_usd) or max_cost_usd <= 0:
        raise ValueError("max_cost_usd must be finite and positive")


def validate_answer(raw_output: str, context: ContextBundle) -> tuple[str, bool, tuple[ValidatedCitation, ...]]:
    """Verify structure and literal citations, not whether a claim is entailed."""
    data = json.loads(raw_output)
    if not isinstance(data, dict) or set(data) != {"answer", "insufficient_evidence", "citations"}:
        raise ValueError("Answer must contain exactly answer, insufficient_evidence, citations")
    answer = data["answer"]
    insufficient = data["insufficient_evidence"]
    if not isinstance(answer, str) or not answer.strip() or type(insufficient) is not bool:
        raise ValueError("Answer needs non-blank text and a boolean insufficient_evidence")
    if not isinstance(data["citations"], list):
        raise ValueError("citations must be an array")
    citations = []
    seen = set()
    mapping = context.citations
    for item in data["citations"]:
        if not isinstance(item, dict) or set(item) != {"evidence_id", "quote"}:
            raise ValueError("Each citation must contain evidence_id and quote")
        identifier, quote = item["evidence_id"], item["quote"]
        if not isinstance(identifier, str) or identifier not in mapping:
            raise ValueError("Answer cites an unknown evidence ID")
        if not isinstance(quote, str) or not normalize_evidence(quote):
            raise ValueError("Citation quote cannot be blank")
        normalized = normalize_evidence(quote)
        if normalized not in normalize_evidence(mapping[identifier].text):
            raise ValueError("Citation quote is absent from its evidence text")
        if (identifier, normalized) in seen:
            raise ValueError("Duplicate citation")
        seen.add((identifier, normalized))
        citations.append(ValidatedCitation(identifier, quote, mapping[identifier].sources))
    inline = set(re.findall(r"\[(E[^\]]*)\]", answer))
    if inline != {citation.evidence_id for citation in citations}:
        raise ValueError("Inline evidence IDs and citation list do not agree")
    if not insufficient and not citations:
        raise ValueError("A supported answer must cite at least one evidence source")
    return answer, insufficient, tuple(citations)


def _usage(response: Any) -> TokenUsage | None:
    usage = response.usage
    if usage is None:
        return None
    cached = getattr(usage.input_tokens_details, "cached_tokens", 0) or 0
    reasoning = getattr(usage.output_tokens_details, "reasoning_tokens", 0) or 0
    values = (usage.input_tokens, cached, usage.output_tokens, reasoning)
    if any(type(value) is not int or value < 0 for value in values) or cached > usage.input_tokens or reasoning > usage.output_tokens:
        raise ValueError("API returned inconsistent token usage")
    return TokenUsage(*values)


class OpenAIAnswerGenerator:
    """One provider adapter; injectable client keeps tests free and offline."""

    model_name = MODEL_NAME

    def __init__(self, *, client: Any = None, timeout: float = 60,
                 token_count_mode: Literal["api", "local"] = "api", trust_env: bool = True) -> None:
        if token_count_mode not in ("api", "local"):
            raise ValueError("token_count_mode must be api or local")
        if client is None:
            client = create_openai_client(timeout=timeout, trust_env=trust_env)
        self._client = client
        self.token_count_mode = token_count_mode
        self._encoding = None
        self._token_counts: dict[str, int] = {}

    def _request_fields(self, messages: Sequence[PromptMessage]) -> dict:
        return {"model": self.model_name, "input": [asdict(message) for message in messages],
                "instructions": GENERATION_INSTRUCTIONS, "reasoning": {"effort": "none"},
                "text": {"format": {"type": "json_schema", "name": "grounded_answer",
                                    "strict": True, "schema": ANSWER_SCHEMA}, "verbosity": "low"},
                "truncation": "disabled"}

    def count_prompt_tokens(self, messages: Sequence[PromptMessage]) -> int:
        fields = self._request_fields(messages)
        key = json.dumps(fields, ensure_ascii=False, sort_keys=True)
        if key not in self._token_counts:
            if self.token_count_mode == "local":
                # Include instructions/schema, not just the retrieved text. This
                # serialized request is an estimate, not the server wire format.
                if self._encoding is None:
                    import tiktoken
                    self._encoding = tiktoken.encoding_for_model(self.model_name)
                tokens = len(self._encoding.encode(key, disallowed_special=()))
                count = (tokens * 110 + 99) // 100 + 128
            else:
                count = self._client.responses.input_tokens.count(**fields).input_tokens
            if type(count) is not int or count < 0:
                raise ValueError("OpenAI token counter returned an invalid count")
            self._token_counts[key] = count
        return self._token_counts[key]

    def generate(self, context: ContextBundle, *, max_output_tokens: int = 512,
                 max_cost_usd: float = 0.02) -> tuple[GeneratedAnswer, float]:
        _validate_generation_limits(max_output_tokens, max_cost_usd)
        if not context.has_evidence:
            return GeneratedAnswer("no_evidence", "检索结果中没有可用于回答的证据。", (), True,
                                   self.model_name, None, TokenUsage(0, 0, 0, 0), ""), 0.0
        # Re-count with this adapter; never trust an E5 preview or caller's count.
        count = self.count_prompt_tokens(context.messages)
        if count > context.max_input_tokens or count + max_output_tokens > CONTEXT_WINDOW:
            raise ValueError("Prompt exceeds the input/context budget; rebuild the context")
        maximum_cost = (count * INPUT_USD_PER_MILLION + max_output_tokens * OUTPUT_USD_PER_MILLION) / 1_000_000
        if maximum_cost > max_cost_usd:
            raise ValueError(f"Generation cost bound/estimate ${maximum_cost:.6f} exceeds max_cost_usd=${max_cost_usd:.6f}")
        response = self._client.responses.create(
            **self._request_fields(context.messages), max_output_tokens=max_output_tokens,
            store=False, service_tier="default",
        )
        raw_output = response.output_text or ""
        usage = _usage(response)
        common = {"model": response.model, "response_id": response.id,
                  "usage": usage, "raw_output": raw_output}
        if response.status != "completed":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
            return GeneratedAnswer("incomplete", None, (), None, **common,
                                   error=f"Response status={response.status}; reason={reason}"), maximum_cost
        refusals = [content.refusal for item in response.output if getattr(item, "type", None) == "message"
                    for content in item.content if getattr(content, "type", None) == "refusal"]
        if refusals:
            return GeneratedAnswer("model_refusal", None, (), None, **common, error="\n".join(refusals)), maximum_cost
        try:
            answer, insufficient, citations = validate_answer(raw_output, context)
        except (ValueError, TypeError) as error:
            return GeneratedAnswer("invalid_response", None, (), None, **common, error=str(error)), maximum_cost
        status = "insufficient_evidence" if insufficient else "answered"
        return GeneratedAnswer(status, answer, citations, insufficient, **common), maximum_cost

    def answer_question(self, question: str, results: Sequence[SearchResult], *,
                        max_input_tokens: int = 4096, max_output_tokens: int = 512,
                        max_cost_usd: float = 0.02) -> AnswerRun:
        _validate_generation_limits(max_output_tokens, max_cost_usd)
        if type(max_input_tokens) is not int or max_input_tokens <= 0 or max_input_tokens + max_output_tokens > CONTEXT_WINDOW:
            raise ValueError("Input budget must be positive and leave room for the output budget")
        context = build_context(question, results, self.count_prompt_tokens,
                                max_input_tokens=max_input_tokens,
                                token_counter_name=(
                                    f"Local tiktoken estimate: {self.model_name}; serialized request including instructions/schema + 10% + 128 tokens; not an exact count or guaranteed cost bound"
                                    if self.token_count_mode == "local" else
                                    f"OpenAI Responses input_tokens.count: {self.model_name}; includes instructions and JSON schema"))
        result, maximum_cost = self.generate(context, max_output_tokens=max_output_tokens, max_cost_usd=max_cost_usd)
        return AnswerRun(context, result, max_output_tokens, maximum_cost, max_cost_usd)
