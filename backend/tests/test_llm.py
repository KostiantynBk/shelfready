"""LLM client: request shape, record/replay, retries, rate limit and mock. No network."""

import asyncio
import base64
import io
import json
from typing import Literal

import httpx2  # the HTTP library the openai SDK uses
import pytest
from PIL import Image
from pydantic import BaseModel, Field

from app.clients.llm import (
    LLMClient,
    LLMError,
    LLMOutputError,
    RateLimiter,
    ReplayMiss,
    _keyed_messages,
    _wire_messages,
    compact_schema,
    fake_from_schema,
    image_part,
    request_key,
    user_message,
)
from app.config import Settings


class Box(BaseModel):
    label: str
    box_2d: list[int] = Field(min_length=4, max_length=4)


class Photo(BaseModel):
    role: Literal["packshot", "detail", "other"]
    legs_material: Literal["wood", "metal", "unsure"]
    colour_family: str
    confidence: float = Field(ge=0, le=1)
    note: str | None = None
    boxes: list[Box]


class Listing(BaseModel):
    title: str
    bullets: list[str]


class FakeTime:
    """A clock that only moves when someone sleeps."""

    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class Endpoint:
    """Scripted OpenAI-compatible endpoint: answers in order and keeps the request bodies."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests: list[dict] = []

    def __call__(self, request):
        self.requests.append(json.loads(request.content))
        status, body, headers = self.replies.pop(0)
        return httpx2.Response(status, json=body, headers=headers)

    def client(self):
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self))


def ok(content=None, tool_calls=None, model="Gemma-4-31B-IT"):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    body = {
        "id": "c1",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}
        ],
        "usage": {"prompt_tokens": 600, "completion_tokens": 90, "total_tokens": 690},
    }
    return 200, body, {}


def settings(tmp_path, **overrides):
    values = {
        "llm_mode": "live",
        "llm_api_key": "test-key",
        "llm_base_url": "http://tf.test/v1",
        "data_dir": tmp_path / "runtime",
        "replay_dir": tmp_path / "replay",
    }
    return Settings(_env_file=None, **(values | overrides))


def png(size=(64, 64), colour="green", mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, colour).save(buf, "PNG")
    return buf.getvalue()


ASK = [{"role": "user", "content": "Write it."}]
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_product",
            "description": "Look up a product by SKU",
            "parameters": {"type": "object", "properties": {"sku": {"type": "string"}}, "required": ["sku"]},
        },
    }
]


# --------------------------------------------------------------------------- helpers (sync)
def test_image_part_flattens_transparency_and_downscales():
    part = image_part(png((3000, 1500), (0, 0, 0, 0), "RGBA"), max_side=1536)
    jpeg = Image.open(io.BytesIO(base64.b64decode(part["image_url"]["url"].split(",", 1)[1])))
    assert jpeg.format == "JPEG"
    assert jpeg.size == (1536, 768)
    assert jpeg.getpixel((10, 10))[0] > 240  # transparent becomes white, not black
    assert part["_digest"].startswith("sha256:") and part["_digest"].endswith("@1536")


def test_user_message_puts_images_before_text(tmp_path):
    photo = tmp_path / "chair.png"
    photo.write_bytes(png())
    message = user_message("What colour?", [photo])
    assert [p["type"] for p in message["content"]] == ["image_url", "text"]


def test_cache_key_follows_the_image_digest_not_the_jpeg_bytes():
    def message(url, digest):
        return {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": url}, "_digest": digest},
                {"type": "text", "text": "hi"},
            ],
        }

    def key(m):
        return request_key({"model": "m", "messages": _keyed_messages([m])})

    same_photo = key(message("data:image/jpeg;base64,AAA", "sha256:1@1536"))
    assert key(message("data:image/jpeg;base64,BBB", "sha256:1@1536")) == same_photo
    assert key(message("data:image/jpeg;base64,AAA", "sha256:2@1536")) != same_photo
    assert "_digest" not in json.dumps(_wire_messages([message("data:x", "sha256:1@1536")]))


def test_compact_schema_drops_annotations_but_keeps_a_title_field():
    schema = compact_schema(Listing)
    assert "title" not in schema  # Pydantic's "Listing" annotation
    assert set(schema["properties"]) == {"title", "bullets"}


def test_fake_from_schema_satisfies_the_model():
    photo = Photo.model_validate(fake_from_schema(compact_schema(Photo)))
    assert photo.role == "packshot"
    assert photo.boxes[0].box_2d == [100, 200, 300, 400]
    assert 0 <= photo.confidence <= 1


# --------------------------------------------------------------------------- mock mode
@pytest.mark.anyio
async def test_mock_mode_answers_json_and_tool_calls_and_logs(tmp_path):
    async with LLMClient(settings(tmp_path, llm_mode="mock")) as llm:
        photo, result = await llm.chat_json(
            [user_message("Describe the chair.", [png()])], Photo, purpose="photo"
        )
        call = await llm.chat(
            [{"role": "user", "content": "Seat height of LIS-01-MUS?"}], purpose="agent", tools=TOOLS
        )
    assert result.source == "mock" and photo.boxes[0].label == "x"
    assert call.tool_calls[0]["function"]["name"] == "get_product"
    assert json.loads(call.tool_calls[0]["function"]["arguments"]) == {"sku": "x"}
    log = [json.loads(line) for line in (tmp_path / "runtime" / "llm_calls.jsonl").read_text().splitlines()]
    assert [(e["purpose"], e["source"]) for e in log] == [("photo", "mock"), ("agent", "mock")]


@pytest.mark.anyio
async def test_mock_responder_overrides_the_fake(tmp_path):
    def responder(purpose, request):
        return {"title": f"{purpose} title", "bullets": ["a"]}

    async with LLMClient(settings(tmp_path, llm_mode="mock"), mock_responder=responder) as llm:
        listing, _ = await llm.chat_json(ASK, Listing, purpose="listing")
    assert listing.title == "listing title"


# --------------------------------------------------------------------------- live, record, replay
@pytest.mark.anyio
async def test_live_json_request_is_recorded_then_replayed_offline(tmp_path):
    endpoint = Endpoint(ok('{"title": "LISBOA Armchair", "bullets": ["Solid oak legs"]}'))
    messages = [{"role": "system", "content": "You write listings."}, *ASK]
    async with LLMClient(settings(tmp_path), http_client=endpoint.client()) as llm:
        listing, result = await llm.chat_json(messages, Listing, purpose="listing")

    assert listing.title == "LISBOA Armchair" and result.source == "live"
    assert result.usage == {"prompt_tokens": 600, "completion_tokens": 90}
    sent = endpoint.requests[0]
    assert sent["model"] == "Gemma-4-31B-IT"
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["temperature"] == 0 and sent["max_tokens"] == 1500
    # The schema joins the caller's system message: Gemma needs exactly one, first
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]
    assert sent["messages"][0]["content"].startswith("You write listings.")
    assert '"bullets"' in sent["messages"][0]["content"]
    assert (tmp_path / "replay" / f"{result.key}.json").exists()

    no_network = Endpoint()  # no scripted replies: any request would fail the test
    async with LLMClient(settings(tmp_path, llm_mode="replay"), http_client=no_network.client()) as llm:
        again, replayed = await llm.chat_json(messages, Listing, purpose="listing")
    assert again == listing and replayed.source == "replay" and not no_network.requests


@pytest.mark.anyio
async def test_images_go_to_the_vision_model_without_private_keys(tmp_path):
    endpoint = Endpoint(ok("green"), ok("done"))
    custom = settings(tmp_path, vlm_model="vision-model", text_model="text-model")
    async with LLMClient(custom, http_client=endpoint.client()) as llm:
        await llm.chat([user_message("What colour?", [png()])], purpose="photo")
        await llm.chat(ASK, purpose="text")
    photo_request, text_request = endpoint.requests
    assert photo_request["model"] == "vision-model" and text_request["model"] == "text-model"
    assert photo_request["messages"][0]["content"][0]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )
    assert "_digest" not in json.dumps(photo_request)


@pytest.mark.anyio
async def test_tool_calls_come_back_in_openai_shape(tmp_path):
    call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "get_product", "arguments": '{"sku": "LIS-01-MUS"}'},
    }
    endpoint = Endpoint(ok(None, tool_calls=[call]))
    async with LLMClient(settings(tmp_path), http_client=endpoint.client()) as llm:
        result = await llm.chat(
            [{"role": "user", "content": "Seat height?"}], purpose="agent", tools=TOOLS, tool_choice="auto"
        )
    assert result.tool_calls == [call] and result.finish_reason == "tool_calls"
    assert endpoint.requests[0]["tool_choice"] == "auto"


@pytest.mark.anyio
async def test_json_schema_mode_for_our_own_vllm(tmp_path):
    endpoint = Endpoint(ok('{"title": "A", "bullets": []}'))
    async with LLMClient(
        settings(tmp_path, llm_json_mode="json_schema"), http_client=endpoint.client()
    ) as llm:
        await llm.chat_json(ASK, Listing, purpose="listing")
    response_format = endpoint.requests[0]["response_format"]
    assert response_format["type"] == "json_schema" and response_format["json_schema"]["name"] == "Listing"
    assert set(response_format["json_schema"]["schema"]["properties"]) == {"title", "bullets"}


@pytest.mark.anyio
async def test_cached_mode_calls_the_model_only_for_new_requests(tmp_path):
    endpoint = Endpoint(ok("first"), ok("second"))
    async with LLMClient(settings(tmp_path, llm_mode="cached"), http_client=endpoint.client()) as llm:
        a = await llm.chat([{"role": "user", "content": "one"}], purpose="t")
        b = await llm.chat([{"role": "user", "content": "one"}], purpose="t", max_tokens=50)  # not in the key
        c = await llm.chat([{"role": "user", "content": "two"}], purpose="t")
    assert (a.source, b.source, c.source) == ("live", "replay", "live")
    assert b.content == "first" and len(endpoint.requests) == 2


@pytest.mark.anyio
async def test_replay_miss_never_calls_the_network(tmp_path):
    no_network = Endpoint()
    async with LLMClient(settings(tmp_path, llm_mode="replay"), http_client=no_network.client()) as llm:
        with pytest.raises(ReplayMiss):
            await llm.chat([{"role": "user", "content": "a prompt nobody recorded"}], purpose="t")
    assert not no_network.requests


# --------------------------------------------------------------------------- validation retry
@pytest.mark.anyio
async def test_invalid_reply_is_retried_once_with_the_error(tmp_path):
    endpoint = Endpoint(ok('{"title": "LISBOA"}'), ok('```json\n{"title": "LISBOA", "bullets": []}\n```'))
    async with LLMClient(settings(tmp_path), http_client=endpoint.client()) as llm:
        listing, result = await llm.chat_json(ASK, Listing, purpose="listing")
    assert listing.bullets == [] and len(endpoint.requests) == 2
    retry = endpoint.requests[1]["messages"]
    assert retry[-2] == {"role": "assistant", "content": '{"title": "LISBOA"}'}
    assert "not valid" in retry[-1]["content"] and "bullets" in retry[-1]["content"]


@pytest.mark.anyio
async def test_reply_still_invalid_after_the_retry_raises(tmp_path):
    endpoint = Endpoint(ok("Sorry, I can't."), ok("Still no JSON."))
    async with LLMClient(settings(tmp_path), http_client=endpoint.client()) as llm:
        with pytest.raises(LLMOutputError) as info:
            await llm.chat_json(ASK, Listing, purpose="listing")
    assert info.value.content == "Still no JSON."
    assert not list((tmp_path / "replay").glob("*.json"))  # failed answers are not kept for replay


@pytest.mark.anyio
async def test_cached_mode_asks_again_after_a_failed_answer(tmp_path):
    # e.g. Token Factory returning token soup for a while (2026-10-13): the next run must not replay it
    endpoint = Endpoint(ok("garbage"), ok("more garbage"), ok('{"title": "A", "bullets": []}'))
    async with LLMClient(settings(tmp_path, llm_mode="cached"), http_client=endpoint.client()) as llm:
        with pytest.raises(LLMOutputError):
            await llm.chat_json(ASK, Listing, purpose="listing")
        listing, result = await llm.chat_json(ASK, Listing, purpose="listing")
    assert listing.title == "A" and result.source == "live" and len(endpoint.requests) == 3


# --------------------------------------------------------------------------- errors and limits
@pytest.mark.anyio
async def test_rate_limited_request_waits_for_retry_after(tmp_path):
    time = FakeTime()
    endpoint = Endpoint((429, {"error": {"message": "rpm limit"}}, {"retry-after": "7"}), ok("hello"))
    async with LLMClient(
        settings(tmp_path), http_client=endpoint.client(), clock=time.clock, sleep=time.sleep
    ) as llm:
        result = await llm.chat(ASK, purpose="t")
    assert result.content == "hello" and len(endpoint.requests) == 2
    assert time.slept == [7.0]


@pytest.mark.anyio
async def test_bad_request_fails_fast_with_the_reason(tmp_path):
    reason = {
        "message": "Model Gemma-4-31B-IT does not support JSON schema output mode",
        "type": "invalid_request_error",
    }
    endpoint = Endpoint((400, reason, {}))
    async with LLMClient(settings(tmp_path), http_client=endpoint.client()) as llm:
        with pytest.raises(LLMError, match="400.*JSON schema"):
            await llm.chat(ASK, purpose="t")
    assert len(endpoint.requests) == 1


@pytest.mark.anyio
async def test_server_errors_back_off_then_give_up_and_are_logged(tmp_path):
    time = FakeTime()
    endpoint = Endpoint(*[(503, {"message": "busy"}, {})] * 3)
    async with LLMClient(
        settings(tmp_path, llm_max_retries=2),
        http_client=endpoint.client(),
        clock=time.clock,
        sleep=time.sleep,
    ) as llm:
        with pytest.raises(LLMError, match="gave up after 3 attempts"):
            await llm.chat(ASK, purpose="t")
    assert time.slept == [1.0, 2.0]
    last = json.loads((tmp_path / "runtime" / "llm_calls.jsonl").read_text().splitlines()[-1])
    assert "gave up" in last["error"]


@pytest.mark.anyio
async def test_live_mode_needs_a_key(tmp_path):
    async with LLMClient(settings(tmp_path, llm_api_key="")) as llm:
        with pytest.raises(LLMError, match="LLM_API_KEY"):
            await llm.chat(ASK, purpose="t")


@pytest.mark.anyio
async def test_system_message_must_come_first(tmp_path):
    async with LLMClient(settings(tmp_path, llm_mode="mock")) as llm:
        with pytest.raises(ValueError, match="first"):
            await llm.chat([*ASK, {"role": "system", "content": "late"}], purpose="t")


@pytest.mark.anyio
async def test_rate_limiter_spreads_starts_over_the_minute():
    time = FakeTime()
    limiter = RateLimiter(rpm=3, concurrency=2, clock=time.clock, sleep=time.sleep)
    starts = []
    for _ in range(5):
        async with limiter.slot():
            starts.append(time.now - 1000)
    assert starts == [0, 0, 0, 60, 60]


@pytest.mark.anyio
async def test_rate_limiter_caps_requests_in_flight():
    limiter = RateLimiter(rpm=100, concurrency=2)
    in_flight = peak = 0

    async def one():
        nonlocal in_flight, peak
        async with limiter.slot():
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1

    await asyncio.gather(*(one() for _ in range(6)))
    assert peak == 2
