"""litellm Gateway 클라이언트 (그룹별 AIGatewaySettings로 생성).

- 경로 A (Gemini native passthrough): POST {base_url}/gemini/v1beta/models/{model}:generateContent?key=...
- 경로 B (OpenAI 호환):              POST {base_url}/v1/chat/completions
- 모델 목록:                         GET  {base_url}/v1/models
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import httpx

from app.services.settings_types import AIGatewaySettings


class LiteLLMError(RuntimeError):
    pass


def _normalize_base_url(raw: str) -> str:
    u = (raw or "").strip()
    if not u:
        return ""
    if u.lower().startswith(("http://", "https://")):
        return u.rstrip("/")
    return f"http://{u}".rstrip("/")


def _strip_code_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        t = "\n".join(line for line in lines if not line.startswith("```")).strip()
    return t


def _pick_text_from_gemini(payload: Dict[str, Any]) -> str:
    try:
        candidates = payload.get("candidates") or []
        if not candidates:
            return ""
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []
        if not parts:
            return ""
        return parts[0].get("text") or ""
    except Exception:
        return ""


@dataclass(frozen=True)
class AnalyzerResult:
    data: Dict[str, Any]
    raw_text: str


@dataclass(frozen=True)
class ChatResult:
    content: str
    raw: Dict[str, Any]


class LiteLLMClient:
    def __init__(
        self,
        settings: AIGatewaySettings,
        client: httpx.AsyncClient | None = None,
        models_cache_ttl_sec: float = 60.0,
    ) -> None:
        base = _normalize_base_url(settings.base_url)
        if not base:
            raise LiteLLMError("AI Gateway base_url이 비어 있습니다.")
        self._settings = settings
        self._base_url = base
        self._client = client or httpx.AsyncClient(timeout=300.0)
        self._models_cache: list[str] | None = None
        self._models_cache_exp = 0.0
        self._models_cache_ttl = models_cache_ttl_sec

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def api_key(self) -> str:
        return self._settings.api_key

    async def get_models(self, force_refresh: bool = False) -> list[str]:
        now = time.monotonic()
        if not force_refresh and self._models_cache is not None and now < self._models_cache_exp:
            return self._models_cache
        resp = await self._client.get(
            f"{self._base_url}/v1/models",
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
        )
        if resp.status_code != 200:
            raise LiteLLMError(f"/v1/models 실패: {resp.status_code} - {resp.text}")
        models = [m["id"] for m in (resp.json().get("data") or []) if m.get("id")]
        self._models_cache = models
        self._models_cache_exp = now + self._models_cache_ttl
        return models

    async def analyze_video_native(
        self,
        model: str,
        video_url: str,
        prompt: str,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> AnalyzerResult:
        """경로 A: Gemini native passthrough (fileData.fileUri 멀티모달)."""
        if not self.api_key:
            raise LiteLLMError("AI Gateway api_key가 비어 있습니다.")
        model_id = model.split("/")[-1] if "/" in model else model
        url = f"{self._base_url}/gemini/v1beta/models/{model_id}:generateContent"
        gen_cfg: Dict[str, Any] = {"responseMimeType": "application/json"}
        if temperature is not None:
            gen_cfg["temperature"] = temperature
        if max_output_tokens is not None:
            gen_cfg["maxOutputTokens"] = max_output_tokens
        body = {
            "contents": [
                {"role": "user", "parts": [{"fileData": {"fileUri": video_url}}, {"text": prompt}]}
            ],
            "generationConfig": gen_cfg,
        }
        resp = await self._client.post(url, params={"key": self.api_key}, json=body)
        if resp.status_code != 200:
            raise LiteLLMError(f"Gemini native 분석 실패: {resp.status_code} - {resp.text}")
        raw_text = _pick_text_from_gemini(resp.json())
        if not raw_text:
            raise LiteLLMError("Gemini 응답에서 텍스트를 찾지 못했습니다.")
        try:
            return AnalyzerResult(data=json.loads(_strip_code_fence(raw_text)), raw_text=raw_text)
        except Exception as e:
            raise LiteLLMError(f"Gemini 구조화 출력 JSON 파싱 실패: {e}") from e

    async def chat(
        self,
        model: str,
        messages: Sequence[Dict[str, Any]],
        response_format: Dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """경로 B: OpenAI 호환 chat completions."""
        if not self.api_key:
            raise LiteLLMError("AI Gateway api_key가 비어 있습니다.")
        body: Dict[str, Any] = {"model": model, "messages": list(messages)}
        if response_format is not None:
            body["response_format"] = response_format
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        resp = await self._client.post(
            f"{self._base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=body,
        )
        if resp.status_code != 200:
            raise LiteLLMError(f"chat completions 실패: {resp.status_code} - {resp.text}")
        payload = resp.json()
        try:
            content = payload["choices"][0]["message"]["content"]
        except Exception as e:
            raise LiteLLMError("chat completions 응답 파싱 실패") from e
        return ChatResult(content=content or "", raw=payload)
