"""One client for every model call (Gemma 4 31B on AMD Token Factory or our own vLLM).

Modes (LLM_MODE):
- live: call the OpenAI-compatible endpoint. With LLM_RECORD on, every answer is saved to REPLAY_DIR.
- replay: answer only from REPLAY_DIR; a missing answer raises ReplayMiss and nothing touches the network.
- cached: replay when an answer is recorded, otherwise live (re-runs only spend requests on new prompts).
- mock: schema-shaped placeholders for tests and UI work.

Structured output: Token Factory's Gemma has no strict json_schema, so chat_json() puts the JSON schema
in the system message, asks for json_object, validates with Pydantic and retries once with the error.

Rate limits are per account (Token Factory: 20 requests/min, 8 in flight), so one RateLimiter per process
keeps us under them and a 429 pauses every caller for Retry-After seconds.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import re
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple, TypeVar

import openai
from PIL import Image, ImageOps
from pydantic import BaseModel

from app.config import Settings, get_settings

T = TypeVar("T", bound=BaseModel)
Message = dict[str, Any]
Sleep = Callable[[float], Awaitable[None]]
MockResponder = Callable[[str, dict[str, Any]], str | dict | None]

MAX_IMAGE_SIDE = 1536  # the spike's setting; Gemma 4 spends ~300 tokens per image either way
RETRYABLE_STATUS = {408, 409, 500, 502, 503, 504}
DEFAULT_RETRY_AFTER_S = 10.0


class LLMError(RuntimeError):
    """The model call was rejected or still failed after retries."""


class LLMOutputError(LLMError):
    """The model answered, but not with JSON that matches the schema (after one retry)."""

    def __init__(self, message: str, content: str | None):
        super().__init__(message)
        self.content = content


class ReplayMiss(LookupError):
    """Replay mode, and no recorded answer matches this request."""


@dataclass
class LLMResult:
    content: str | None
    tool_calls: list[dict[str, Any]]
    model: str
    source: str  # live | replay | mock
    key: str  # replay cache key (sha256 of the request)
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    latency_s: float = 0.0


# --------------------------------------------------------------------------- messages and images
class ImageRef(NamedTuple):
    """An image we rendered ourselves (e.g. a PDF page) with a stable identity for the replay cache,
    since rendered bytes can differ between machines while the source document does not."""

    source: Path | bytes
    digest: str


def image_part(image: Path | bytes | ImageRef, max_side: int = MAX_IMAGE_SIDE) -> dict[str, Any]:
    """An image_url content part: JPEG data URL, longest side <= max_side, transparency on white.

    `_digest` (hash of the original bytes, or an ImageRef's digest) is the image's replay-cache
    identity, so recorded answers survive JPEG encoder changes; it is stripped before sending.
    """
    digest = None
    if isinstance(image, ImageRef):
        image, digest = image.source, image.digest
    raw = image.read_bytes() if isinstance(image, Path) else image
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        im = Image.new("RGB", rgba.size, "white")
        im.paste(rgba, mask=rgba.getchannel("A"))
    else:
        im = im.convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()},
        "_digest": f"{digest or 'sha256:' + hashlib.sha256(raw).hexdigest()}@{max_side}",
    }


def user_message(text: str, images: Iterable[Path | bytes | ImageRef] = ()) -> Message:
    """A user message with the images first, then the text (the order the spike used)."""
    content: list[dict[str, Any]] = [image_part(i) for i in images]
    content.append({"type": "text", "text": text})
    return {"role": "user", "content": content}


def _parts(message: Message) -> list[dict[str, Any]]:
    content = message.get("content")
    return content if isinstance(content, list) else []


def _has_images(messages: list[Message]) -> bool:
    return any(p.get("type") == "image_url" for m in messages for p in _parts(m))


def _wire_messages(messages: list[Message]) -> list[Message]:
    """Messages as sent: private keys like `_digest` removed from content parts."""
    return [
        {**m, "content": [{k: v for k, v in p.items() if not k.startswith("_")} for p in _parts(m)]}
        if _parts(m)
        else m
        for m in messages
    ]


def _keyed_messages(messages: list[Message]) -> list[Message]:
    """Messages for the cache key and the recording: image data replaced by a short digest."""

    def keyed(part: dict[str, Any]) -> dict[str, Any]:
        if part.get("type") != "image_url":
            return {k: v for k, v in part.items() if not k.startswith("_")}
        url = part.get("_digest") or part["image_url"]["url"]
        if url.startswith("data:"):
            url = "sha256:" + hashlib.sha256(url.encode()).hexdigest()
        return {"type": "image_url", "image_url": {"url": url}}

    return [{**m, "content": [keyed(p) for p in _parts(m)]} if _parts(m) else m for m in messages]


def request_key(request: dict[str, Any]) -> str:
    """Stable hash of a request in keyed form. max_tokens is left out so tuning it keeps old recordings."""
    canonical = json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _check_system_first(messages: list[Message]) -> None:
    if any(m.get("role") == "system" for m in messages[1:]):
        raise ValueError("Only the first message may be a system message (Gemma requires it first)")


def _with_system(messages: list[Message], text: str) -> list[Message]:
    """Add text to the leading system message, or start with one."""
    if messages and messages[0].get("role") == "system":
        return [{**messages[0], "content": f"{messages[0]['content']}\n\n{text}"}, *messages[1:]]
    return [{"role": "system", "content": text}, *messages]


# --------------------------------------------------------------------------- JSON helpers
def compact_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The model's JSON schema without Pydantic's "title" annotations (fields named "title" stay)."""

    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if not (k == "title" and isinstance(v, str))}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return strip(model.model_json_schema())


def schema_instruction(schema: dict[str, Any]) -> str:
    # Same wording as the Token Factory check (spike/tf_check.py), which passed every test
    return "Reply with one JSON object only, matching this JSON schema:\n" + json.dumps(
        schema, ensure_ascii=False
    )


def parse_json_object(text: str | None) -> Any:
    """The JSON object in a reply, tolerating markdown fences or a sentence around it."""
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("the reply contains no JSON object")
    return json.loads(text[start : end + 1])


def fake_from_schema(schema: dict[str, Any], defs: dict[str, Any] | None = None) -> Any:
    """A deterministic value that satisfies a (Pydantic-generated) JSON schema. Used by mock mode."""
    defs = schema.get("$defs", {}) if defs is None else defs
    if "$ref" in schema:
        return fake_from_schema(defs[schema["$ref"].rsplit("/", 1)[-1]], defs)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [s for s in schema[key] if s.get("type") != "null"] or schema[key]
            return fake_from_schema(options[0], defs)
    if "allOf" in schema:
        return fake_from_schema(schema["allOf"][0], defs)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind == "object" or "properties" in schema:
        return {name: fake_from_schema(sub, defs) for name, sub in schema.get("properties", {}).items()}
    if kind == "array":
        items = schema.get("items", {})
        n = max(schema.get("minItems", 1), 1)
        if items.get("type") == "integer" and "minimum" not in items and "maximum" not in items:
            return [100 * (i + 1) for i in range(n)]  # looks like a box_2d in 0-1000
        return [fake_from_schema(items, defs) for _ in range(n)]
    if kind in ("integer", "number"):
        value = 1 if kind == "integer" else 0.5
        if "minimum" in schema:
            value = max(value, schema["minimum"])
        if "maximum" in schema:
            value = min(value, schema["maximum"])
        return value
    if kind == "string":
        return "x" * max(schema.get("minLength", 1), 1)
    return {"boolean": False, "null": None}.get(kind)


# --------------------------------------------------------------------------- rate limiting
class RateLimiter:
    """At most `rpm` request starts in any 60 s window and `concurrency` requests in flight.

    pause() holds every caller back (after a 429), since the limit belongs to the account.
    """

    def __init__(
        self,
        rpm: int,
        concurrency: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ):
        self.rpm = rpm
        self._clock, self._sleep = clock, sleep
        self._starts: deque[float] = deque()
        self._paused_until = 0.0
        self._turn = asyncio.Lock()
        self._slots = asyncio.Semaphore(concurrency)

    def pause(self, seconds: float) -> None:
        self._paused_until = max(self._paused_until, self._clock() + seconds)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        async with self._slots:
            await self._wait_turn()
            yield

    async def _wait_turn(self) -> None:
        async with self._turn:  # one waiter at a time keeps the order fair
            while True:
                now = self._clock()
                while self._starts and now - self._starts[0] >= 60:
                    self._starts.popleft()
                wait = self._paused_until - now
                if wait <= 0 and len(self._starts) >= self.rpm:
                    wait = 60 - (now - self._starts[0])
                if wait <= 0:
                    self._starts.append(now)
                    return
                await self._sleep(wait)


def _retry_after(error: openai.APIStatusError) -> float:
    try:
        return min(float(error.response.headers.get("retry-after", DEFAULT_RETRY_AFTER_S)), 120.0)
    except ValueError:
        return DEFAULT_RETRY_AFTER_S


def _error_text(error: openai.APIStatusError) -> str:
    body = error.body
    if isinstance(body, dict):
        return str(body.get("message") or body.get("error") or body)[:300]
    return str(error)[:300]


# --------------------------------------------------------------------------- client
class LLMClient:
    """Async model client. One per process (the rate limit is shared); use `async with` or aclose()."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: Any = None,
        mock_responder: MockResponder | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ):
        self.settings = settings or get_settings()
        self.limiter = RateLimiter(
            self.settings.llm_rpm, self.settings.llm_concurrency, clock=clock, sleep=sleep
        )
        self._http_client = http_client  # tests inject a mock transport here
        self._mock_responder = mock_responder
        self._clock, self._sleep = clock, sleep
        self._openai: openai.AsyncOpenAI | None = None

    async def __aenter__(self) -> LLMClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._openai is not None:
            await self._openai.close()
            self._openai = None

    async def chat(
        self,
        messages: list[Message],
        *,
        purpose: str,
        model: str | None = None,
        max_tokens: int = 1200,
        temperature: float = 0.0,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResult:
        """A plain chat completion (text or tool calls). `purpose` labels logs and recordings."""
        return await self._complete(
            messages,
            purpose=purpose,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            tool_choice=tool_choice,
        )

    async def chat_json(
        self,
        messages: list[Message],
        schema: type[T],
        *,
        purpose: str,
        model: str | None = None,
        max_tokens: int = 1500,
        temperature: float = 0.0,
    ) -> tuple[T, LLMResult]:
        """A reply validated as `schema`. Retries once, telling the model what was wrong."""
        json_schema = compact_schema(schema)
        messages = _with_system(messages, schema_instruction(json_schema))
        if self.settings.llm_json_mode == "json_schema":
            name = re.sub(r"[^A-Za-z0-9_-]", "_", schema.__name__)
            response_format = {"type": "json_schema", "json_schema": {"name": name, "schema": json_schema}}
        else:
            response_format = {"type": "json_object"}

        async def ask(msgs: list[Message], label: str) -> LLMResult:
            return await self._complete(
                msgs,
                purpose=label,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
                mock_schema=json_schema,
            )

        first_result = await ask(messages, purpose)
        try:
            return schema.model_validate(parse_json_object(first_result.content)), first_result
        except ValueError as first:  # JSONDecodeError and ValidationError are ValueErrors
            feedback = (
                f"That reply was not valid: {str(first)[:500]}\n"
                "Reply again with one corrected JSON object only."
            )
            retry = [
                *messages,
                {"role": "assistant", "content": first_result.content or ""},
                {"role": "user", "content": feedback},
            ]
            result = await ask(retry, f"{purpose}:retry")
            try:
                return schema.model_validate(parse_json_object(result.content)), result
            except ValueError as second:
                # Don't keep failed answers: replaying them would repeat the failure on the next run
                self._forget(first_result.key)
                self._forget(result.key)
                raise LLMOutputError(
                    f"{purpose}: reply does not match {schema.__name__} after one retry: {str(second)[:300]}",
                    result.content,
                ) from second

    # ----------------------------------------------------------------------- internals
    async def _complete(
        self,
        messages: list[Message],
        *,
        purpose: str,
        model: str | None,
        max_tokens: int,
        temperature: float,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
        mock_schema: dict[str, Any] | None = None,
    ) -> LLMResult:
        _check_system_first(messages)
        model = model or (self.settings.vlm_model if _has_images(messages) else self.settings.text_model)
        request: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
        if response_format:
            request["response_format"] = response_format
        if tools:
            request["tools"] = tools
        if tool_choice:
            request["tool_choice"] = tool_choice
        keyed = {**request, "messages": _keyed_messages(messages)}
        key = request_key(keyed)
        mode = self.settings.llm_mode
        try:
            if mode == "mock":
                result = self._mock(purpose, request, key, mock_schema)
            elif mode in ("replay", "cached") and (recorded := self._load(key)) is not None:
                result = recorded
            elif mode == "replay":
                raise ReplayMiss(f"{purpose}: no recorded answer {key[:12]} in {self.settings.replay_dir}")
            else:
                wire = {**request, "messages": _wire_messages(messages), "max_tokens": max_tokens}
                result = await self._live(wire, purpose, key)
                if self.settings.llm_record:
                    self._record(key, purpose, keyed, result)
        except Exception as error:
            self._log(purpose, mode, model, key, error=f"{type(error).__name__}: {str(error)[:200]}")
            raise
        self._log(purpose, result.source, result.model, key, result=result)
        return result

    def _client(self) -> openai.AsyncOpenAI:
        if self._openai is None:
            api_key = self.settings.llm_api_key.get_secret_value()
            if not api_key or not self.settings.llm_base_url:
                raise LLMError("LLM_API_KEY and LLM_BASE_URL must be set for live or cached mode")
            self._openai = openai.AsyncOpenAI(
                base_url=self.settings.llm_base_url,
                api_key=api_key,
                timeout=self.settings.llm_timeout_s,
                max_retries=0,
                http_client=self._http_client,
            )
        return self._openai

    async def _live(self, wire: dict[str, Any], purpose: str, key: str) -> LLMResult:
        client = self._client()
        attempts = self.settings.llm_max_retries + 1
        last: Exception | None = None
        for attempt in range(attempts):
            backoff = 0.0
            async with self.limiter.slot():
                started = self._clock()
                try:
                    completion = await client.chat.completions.create(**wire)
                except openai.RateLimitError as error:
                    self.limiter.pause(_retry_after(error))  # the next slot() waits it out
                    last = error
                except openai.APIStatusError as error:
                    if error.status_code not in RETRYABLE_STATUS:
                        raise LLMError(
                            f"{purpose}: model endpoint returned {error.status_code}: {_error_text(error)}"
                        ) from error
                    last, backoff = error, 2.0**attempt
                except openai.APIConnectionError as error:  # includes timeouts
                    last, backoff = error, 2.0**attempt
                else:
                    choice = completion.choices[0]
                    usage = completion.usage
                    return LLMResult(
                        content=choice.message.content,
                        tool_calls=[
                            tc.model_dump(exclude_none=True) for tc in (choice.message.tool_calls or [])
                        ],
                        model=completion.model or wire["model"],
                        source="live",
                        key=key,
                        finish_reason=choice.finish_reason,
                        usage={
                            "prompt_tokens": usage.prompt_tokens,
                            "completion_tokens": usage.completion_tokens,
                        }
                        if usage
                        else {},
                        latency_s=round(self._clock() - started, 3),
                    )
            if backoff and attempt < attempts - 1:
                await self._sleep(backoff)  # outside the slot, so others can use it meanwhile
        raise LLMError(f"{purpose}: gave up after {attempts} attempts: {last}") from last

    def _mock(
        self, purpose: str, request: dict[str, Any], key: str, schema: dict[str, Any] | None
    ) -> LLMResult:
        reply = self._mock_responder(purpose, request) if self._mock_responder else None
        tool_calls: list[dict[str, Any]] = []
        if reply is None and request.get("tools"):
            fn = request["tools"][0]["function"]
            args = fake_from_schema(fn.get("parameters", {"type": "object"}))
            tool_calls = [
                {
                    "id": "mock-call-1",
                    "type": "function",
                    "function": {"name": fn["name"], "arguments": json.dumps(args)},
                }
            ]
            content = None
        elif reply is None:
            content = json.dumps(fake_from_schema(schema)) if schema else f"[mock reply for {purpose}]"
        else:
            content = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return LLMResult(
            content=content,
            tool_calls=tool_calls,
            model=request["model"],
            source="mock",
            key=key,
            finish_reason="tool_calls" if tool_calls else "stop",
        )

    def _record(self, key: str, purpose: str, keyed: dict[str, Any], result: LLMResult) -> None:
        payload = {
            "key": key,
            "purpose": purpose,
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "request": keyed,
            "response": {k: v for k, v in asdict(result).items() if k not in ("source", "key")},
        }
        path = self.settings.replay_dir / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def _forget(self, key: str) -> None:
        if self.settings.llm_mode in ("live", "cached"):  # replay mode never changes the recordings
            (self.settings.replay_dir / f"{key}.json").unlink(missing_ok=True)

    def _load(self, key: str) -> LLMResult | None:
        path = self.settings.replay_dir / f"{key}.json"
        if not path.exists():
            return None
        response = json.loads(path.read_text(encoding="utf-8"))["response"]
        return LLMResult(**response, source="replay", key=key)

    def _log(
        self,
        purpose: str,
        source: str,
        model: str,
        key: str,
        *,
        result: LLMResult | None = None,
        error: str | None = None,
    ) -> None:
        """One JSON line per call in DATA_DIR/llm_calls.jsonl (usage evidence, latency, failures)."""
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "purpose": purpose,
            "source": source,
            "model": model,
            "key": key[:16],
        }
        if result is not None:
            entry |= {"latency_s": result.latency_s, "finish_reason": result.finish_reason, **result.usage}
        if error is not None:
            entry["error"] = error
        log_path = Path(self.settings.data_dir) / "llm_calls.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
