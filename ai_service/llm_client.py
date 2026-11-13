"""
llm_client.py — Gọi Google Gemini qua REST (generateContent), không cần SDK.

    POST https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent
    Header: x-goog-api-key: <GEMINI_API_KEY>

- Yêu cầu trả JSON theo schema (responseMimeType + responseSchema).
- Nếu model không nhận schema/thinkingConfig (HTTP 400) → tự gửi lại không kèm phần đó.
- HTTP 429 (hết quota) / 5xx / timeout → tạm "nghỉ" model đó và thử model kế tiếp.
- Hết model → ném LLMError để bộ quyết định chuyển sang luật dự phòng.
"""
import json
import logging
import os
import re
import time
from typing import Optional

import requests

logger = logging.getLogger("gemini")

# Có thể ghi đè bằng biến môi trường (chỉ dùng khi test với server giả lập)
API_URL = os.getenv(
    "GEMINI_API_URL",
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
)


class LLMError(Exception):
    pass


class _Retryable(Exception):
    def __init__(self, msg: str, cooldown: float = 60):
        super().__init__(msg)
        self.cooldown = cooldown


def extract_json(text: str) -> dict:
    """Lấy object JSON từ text (chịu được ```json ... ``` hoặc chữ thừa xung quanh)."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise LLMError(f"Phản hồi không chứa JSON: {text[:200]}")
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LLMError(f"JSON không hợp lệ: {exc}; text={text[:200]}") from exc
    if not isinstance(data, dict):
        raise LLMError(f"JSON không phải object: {data!r}")
    return data


class GeminiClient:
    def __init__(self, api_key: str, models: list[str], timeout: float = 30, thinking_level: str = "low"):
        if not api_key:
            raise ValueError("Thiếu GEMINI_API_KEY")
        if not models:
            raise ValueError("Thiếu GEMINI_MODELS")
        self.api_key = api_key
        self.models = models
        self.timeout = timeout
        self.thinking_level = thinking_level
        self.session = requests.Session()
        self._cooldown_until: dict[str, float] = {}
        # Ghi nhớ model nào không hỗ trợ schema/thinking để lần sau khỏi thử lại
        self._no_schema: set[str] = set()
        self._no_thinking: set[str] = set()
        self.last_model: Optional[str] = None

    # ------------------------------------------------------------------
    def generate_json(self, system_prompt: str, user_prompt: str, schema: Optional[dict] = None) -> dict:
        errors = []
        now = time.monotonic()
        for model in self.models:
            if self._cooldown_until.get(model, 0) > now:
                errors.append(f"{model}: đang tạm nghỉ do lỗi/quota")
                continue
            try:
                text = self._call_with_fallbacks(model, system_prompt, user_prompt, schema)
                self.last_model = model
                return extract_json(text)
            except _Retryable as exc:
                self._cooldown_until[model] = time.monotonic() + exc.cooldown
                logger.warning("%s: %s → nghỉ %.0fs, thử model khác", model, exc, exc.cooldown)
                errors.append(f"{model}: {exc}")
            except LLMError as exc:
                logger.warning("%s: %s", model, exc)
                errors.append(f"{model}: {exc}")
        raise LLMError("; ".join(errors) or "Không có model khả dụng")

    # ------------------------------------------------------------------
    def _call_with_fallbacks(self, model, system_prompt, user_prompt, schema) -> str:
        use_schema = schema is not None and model not in self._no_schema
        use_thinking = bool(self.thinking_level) and model not in self._no_thinking
        for _ in range(3):
            try:
                return self._call(model, system_prompt, user_prompt,
                                  schema if use_schema else None,
                                  self.thinking_level if use_thinking else "")
            except _BadRequest as exc:
                msg = str(exc).lower()
                if use_thinking and "think" in msg:
                    self._no_thinking.add(model)
                    use_thinking = False
                    logger.info("%s không nhận thinkingConfig → gửi lại không kèm", model)
                elif use_schema and ("schema" in msg or "response_mime" in msg or "responsemime" in msg):
                    self._no_schema.add(model)
                    use_schema = False
                    logger.info("%s không nhận responseSchema → gửi lại chỉ yêu cầu JSON", model)
                else:
                    raise LLMError(f"HTTP 400: {exc}") from exc
        raise LLMError("Gửi lại quá nhiều lần")

    def _call(self, model, system_prompt, user_prompt, schema, thinking_level) -> str:
        generation_config: dict = {"responseMimeType": "application/json", "maxOutputTokens": 2048}
        if schema:
            generation_config["responseSchema"] = schema
        if thinking_level:
            generation_config["thinkingConfig"] = {"thinkingLevel": thinking_level}

        body = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": generation_config,
        }
        try:
            resp = self.session.post(
                API_URL.format(model=model),
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json=body,
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            raise _Retryable("timeout", cooldown=30) from exc
        except requests.RequestException as exc:
            raise _Retryable(f"lỗi kết nối: {exc}", cooldown=30) from exc

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            cooldown = float(retry_after) if retry_after and retry_after.isdigit() else 60
            raise _Retryable("HTTP 429 hết quota / quá nhiều request", cooldown=cooldown)
        if resp.status_code >= 500:
            raise _Retryable(f"HTTP {resp.status_code} lỗi server", cooldown=30)
        if resp.status_code == 400:
            raise _BadRequest(_error_message(resp))
        if resp.status_code in (401, 403):
            raise LLMError(f"HTTP {resp.status_code}: API key sai hoặc không có quyền ({_error_message(resp)})")
        if resp.status_code == 404:
            raise LLMError(f"HTTP 404: không có model '{model}' (kiểm tra GEMINI_MODELS)")
        if not resp.ok:
            raise LLMError(f"HTTP {resp.status_code}: {_error_message(resp)}")

        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "không rõ")
            raise LLMError(f"Gemini không trả kết quả (blockReason={reason})")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text.strip():
            raise LLMError(f"Phản hồi rỗng (finishReason={candidates[0].get('finishReason')})")
        return text


class _BadRequest(Exception):
    pass


def _error_message(resp: requests.Response) -> str:
    try:
        return resp.json().get("error", {}).get("message", "")[:300]
    except ValueError:
        return resp.text[:300]
