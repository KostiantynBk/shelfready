# ruff: noqa: E501  (request lines)
"""Evolus (https://evolus.ai/en/developers): the email-writing AI Tool, the knowledge library of verified product
facts, and the shopper agent that answers from that library. The agent runs on the model connected in the Evolus
workspace (Gemma 4 31B on AMD). Limit: 60 requests/min per workspace; a 429 carries Retry-After."""

from __future__ import annotations

import time

import httpx

from app.config import Settings


class EvolusError(RuntimeError):
    """Evolus is not connected on this server, or the call failed."""


class Evolus:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.key = settings.evolus_api_key.get_secret_value()
        self.app_id = settings.evolus_app_id
        self.rest = settings.evolus_api_base.rstrip("/")
        self.chat_base = self.rest.removesuffix("/api/v1") + "/v1"  # agent chat is OpenAI-compatible
        self.client = httpx.Client(timeout=90, transport=transport)

    @property
    def configured(self) -> bool:
        return bool(self.key and self.app_id)

    def _send(self, method: str, url: str, **kwargs) -> httpx.Response:
        if not self.configured:
            raise EvolusError("Evolus is not connected on this server.")
        for attempt in range(3):
            response = self.client.request(method, url, **kwargs)
            if response.status_code == 429 and attempt < 2:
                time.sleep(min(float(response.headers.get("Retry-After", 5)), 20))
                continue
            if response.status_code >= 400:
                raise EvolusError(f"Evolus answered {response.status_code}: {response.text[:200]}")
            return response
        raise EvolusError("Evolus is busy (rate limit); try again in a minute.")

    def _rest_headers(self) -> dict[str, str]:
        return {"X-ApiKey": self.key, "X-Application-Id": self.app_id}

    def write_email(self, text: str, tone: str = "formal") -> str:
        response = self._send("POST", f"{self.rest}/ai-tools/email/run", headers=self._rest_headers(),
                              json={"text": text, "options": {"mode": "write", "tone": tone}})  # fmt: skip
        return str(response.json().get("output") or "").strip()

    def upload_document(self, library_id: str, filename: str, content: bytes, language: str = "en") -> dict:
        response = self._send("POST", f"{self.rest}/memories/{library_id}/documents", params={"language": language},
                              headers=self._rest_headers(), files={"file": (filename, content, "text/markdown")})  # fmt: skip
        return response.json()

    def ask(self, agent_id: str, question: str, conversation_id: str | None = None) -> tuple[str, str | None]:
        headers = {"Authorization": f"Bearer {self.key}"}
        if conversation_id:
            headers["X-Conversation-Id"] = conversation_id
        response = self._send("POST", f"{self.chat_base}/chat/completions", headers=headers,
                              json={"model": f"{self.app_id}:{agent_id}",
                                    "messages": [{"role": "user", "content": question}]})  # fmt: skip
        answer = response.json()["choices"][0]["message"]["content"] or ""
        return answer.strip(), response.headers.get("X-Conversation-Id") or conversation_id
