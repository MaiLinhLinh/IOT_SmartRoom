"""
backend_client.py — Gọi các API dành cho AI của backend (xem backend/api.md mục 3, 3b, 6).

    POST /api/people/count   ← đẩy số người (X-API-Key = PEOPLE_API_KEY nếu có)
    GET  /api/ai/context     ← dữ liệu để ra quyết định
    POST /api/ai/command     ← gửi quyết định (X-API-Key = AI_API_KEY nếu có)
    GET  /api/camera/snapshot← ảnh JPEG qua backend
"""
import logging
from typing import Optional

import requests

logger = logging.getLogger("backend")


class BackendError(Exception):
    pass


class ModeIsManual(BackendError):
    """Backend trả 409: thiết bị đang MANUAL nên AI không được điều khiển."""


class BackendClient:
    def __init__(self, base_url: str, people_api_key: str = "", ai_api_key: str = "", timeout: float = 8):
        self.base_url = base_url.rstrip("/")
        self.people_api_key = people_api_key
        self.ai_api_key = ai_api_key
        self.timeout = timeout
        self.session = requests.Session()

    # ---------- helpers ----------
    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    @staticmethod
    def _headers(api_key: str) -> dict:
        return {"X-API-Key": api_key} if api_key else {}

    @staticmethod
    def _raise(resp: requests.Response, what: str) -> None:
        if resp.ok:
            return
        try:
            detail = resp.json().get("detail")
        except ValueError:
            detail = resp.text[:200]
        if resp.status_code == 409:
            raise ModeIsManual(detail or "Thiết bị đang MANUAL")
        raise BackendError(f"{what} → HTTP {resp.status_code}: {detail}")

    # ---------- API ----------
    def get_context(self) -> dict:
        try:
            resp = self.session.get(self._url("/api/ai/context"), timeout=self.timeout)
        except requests.RequestException as exc:
            raise BackendError(f"GET /api/ai/context lỗi kết nối: {exc}") from exc
        self._raise(resp, "GET /api/ai/context")
        return resp.json()

    def push_people_count(self, count: int, source: str = "YOLO") -> dict:
        try:
            resp = self.session.post(
                self._url("/api/people/count"),
                json={"count": int(count), "source": source},
                headers=self._headers(self.people_api_key),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise BackendError(f"POST /api/people/count lỗi kết nối: {exc}") from exc
        self._raise(resp, "POST /api/people/count")
        return resp.json()

    def send_command(self, fan_level: int, ac_on: bool, ac_temp: int, ai_reason: str) -> dict:
        body = {
            "fan_level": int(fan_level),
            "ac_on": bool(ac_on),
            "ac_temp": int(ac_temp),
            "ai_reason": ai_reason[:500],
        }
        try:
            resp = self.session.post(
                self._url("/api/ai/command"),
                json=body,
                headers=self._headers(self.ai_api_key),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise BackendError(f"POST /api/ai/command lỗi kết nối: {exc}") from exc
        self._raise(resp, "POST /api/ai/command")
        return resp.json()

    def get_snapshot(self, url: Optional[str] = None) -> bytes:
        """Lấy JPEG; mặc định qua backend /api/camera/snapshot."""
        target = url or self._url("/api/camera/snapshot")
        try:
            resp = self.session.get(target, timeout=self.timeout)
        except requests.RequestException as exc:
            raise BackendError(f"GET {target} lỗi kết nối: {exc}") from exc
        self._raise(resp, f"GET {target}")
        return resp.content
