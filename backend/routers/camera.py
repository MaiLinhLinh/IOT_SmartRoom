"""
routers/camera.py — Camera proxy cho cả LAN và truy cập từ xa

GET /api/camera/snapshot    ← Backend lấy 1 ảnh JPEG từ CAM
GET /api/camera/stream      ← Proxy MJPEG (cần token ngắn hạn)
GET /api/camera/stream-url  ← Trả URL LAN + URL proxy
GET /api/camera/status      ← Kiểm tra CAM online/offline
"""
import asyncio
import os
import threading
from typing import Optional

import requests
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from database import get_db
from models import User
from routers.auth import ALGORITHM, SECRET_KEY

load_dotenv()

CAM_IP          = os.getenv("CAM_IP", "192.168.1.100")
CAM_PORT        = os.getenv("CAM_PORT", "80")
CAM_STREAM_PORT = os.getenv("CAM_STREAM_PORT", "81")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

try:
    MAX_STREAM_CLIENTS = max(1, int(os.getenv("CAM_STREAM_MAX_CLIENTS", "2")))
except ValueError:
    MAX_STREAM_CLIENTS = 2

try:
    STREAM_READ_TIMEOUT = max(5, int(os.getenv("CAM_STREAM_READ_TIMEOUT_SECONDS", "30")))
except ValueError:
    STREAM_READ_TIMEOUT = 30

router = APIRouter(prefix="/api/camera", tags=["Camera"])

_stream_clients = 0
_stream_lock = threading.Lock()


def _authenticate_stream(
    token: Optional[str] = Query(default=None, description="JWT access token hoặc stream token"),
    authorization: Optional[str] = Header(default=None, description="Bearer <token>"),
    db: Session = Depends(get_db),
) -> User:
    raw = token
    if not raw and authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    if not raw:
        raise HTTPException(
            status_code=401,
            detail="Thiếu token xem camera. Gọi POST /auth/stream-token trước.",
        )
    try:
        payload = jwt.decode(raw, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Token camera không hợp lệ hoặc đã hết hạn")

    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="Token camera không hợp lệ")

    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="Người dùng không tồn tại")
    return user


def _acquire_slot() -> None:
    global _stream_clients
    with _stream_lock:
        if _stream_clients >= MAX_STREAM_CLIENTS:
            raise HTTPException(
                status_code=429,
                detail=f"Đang có {_stream_clients} người xem camera. Thử lại sau.",
            )
        _stream_clients += 1


def _release_slot() -> None:
    global _stream_clients
    with _stream_lock:
        _stream_clients = max(0, _stream_clients - 1)


class _StreamSlot:
    """Đảm bảo mỗi lượt stream chỉ trả slot đúng 1 lần."""

    def __init__(self):
        self._released = False
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        _release_slot()


@router.get("/snapshot", summary="Lấy 1 ảnh JPEG từ ESP32-CAM")
def get_snapshot():
    """
    Backend proxy đến ESP32-CAM để lấy 1 frame JPEG.
    AI engine gọi endpoint này để đếm người (Phase 4).
    Flutter cũng có thể gọi để hiển thị ảnh trên màn hình Camera.

    ESP32-CAM snapshot URL: http://<CAM_IP>/capture
    """
    url = f"http://{CAM_IP}:{CAM_PORT}/capture"
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
    except requests.exceptions.ConnectTimeout:
        raise HTTPException(
            status_code=503,
            detail=f"Không kết nối được ESP32-CAM tại {url}. Kiểm tra IP trong .env"
        )
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=503, detail=f"Lỗi kết nối ESP32-CAM: {str(e)}")

    return Response(
        content      = resp.content,
        media_type   = "image/jpeg",
        headers      = {"Cache-Control": "no-cache"},
    )


@router.get("/stream", summary="Proxy MJPEG để xem camera từ xa")
async def stream_camera(_user: User = Depends(_authenticate_stream)):
    """
    Backend giữ kết nối tới ESP32-CAM và chuyển tiếp MJPEG cho client.
    Client xa (4G) chỉ cần gọi:
        GET /api/camera/stream?token=<stream_token>

    Token lấy từ POST /auth/stream-token (sống ngắn), hoặc dùng access token JWT.
    """
    _acquire_slot()
    slot = _StreamSlot()

    url = f"http://{CAM_IP}:{CAM_STREAM_PORT}/stream"
    try:
        upstream = await asyncio.to_thread(
            requests.get, url, stream=True, timeout=(5, STREAM_READ_TIMEOUT)
        )
        upstream.raise_for_status()
    except requests.exceptions.RequestException as exc:
        slot.release()
        raise HTTPException(503, f"Không kết nối được ESP32-CAM tại {url}: {exc}")

    content_type = upstream.headers.get(
        "Content-Type", "multipart/x-mixed-replace; boundary=frame"
    )
    chunks = upstream.iter_content(chunk_size=8192)

    async def iter_mjpeg():
        try:
            while True:
                chunk = await asyncio.to_thread(next, chunks, None)
                if chunk is None:
                    break
                if chunk:
                    yield chunk
        finally:
            upstream.close()
            slot.release()

    return StreamingResponse(
        iter_mjpeg(),
        media_type=content_type,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
        background=BackgroundTask(slot.release),
    )


@router.get("/stream-url", summary="Trả về URL stream LAN và URL proxy")
def get_stream_url():
    """
    - stream_url / lan_stream_url: URL trực tiếp ESP32-CAM, chỉ dùng trong LAN.
    - proxy_stream_url: URL qua backend; client 4G dùng URL này + ?token=...
    - Nếu PUBLIC_BASE_URL trống, proxy_stream_url là đường dẫn tương đối.
    """
    lan_stream = f"http://{CAM_IP}:{CAM_STREAM_PORT}/stream"
    proxy_path = "/api/camera/stream"
    proxy_url = f"{PUBLIC_BASE_URL}{proxy_path}" if PUBLIC_BASE_URL else proxy_path
    proxy_snapshot = (
        f"{PUBLIC_BASE_URL}/api/camera/snapshot"
        if PUBLIC_BASE_URL else "/api/camera/snapshot"
    )

    return {
        "stream_url":          lan_stream,
        "lan_stream_url":      lan_stream,
        "snapshot_url":        f"http://{CAM_IP}:{CAM_PORT}/capture",
        "proxy_stream_path":   proxy_path,
        "proxy_stream_url":    proxy_url,
        "proxy_snapshot_url":  proxy_snapshot,
        "requires_token":      True,
        "max_stream_clients":  MAX_STREAM_CLIENTS,
        "cam_ip":              CAM_IP,
        "cam_port":            CAM_PORT,
        "cam_stream_port":     CAM_STREAM_PORT,
    }


@router.get("/status", summary="Kiểm tra ESP32-CAM có kết nối được không")
def get_camera_status():
    """Flutter hiển thị trạng thái Online/Offline cho trang Camera."""
    try:
        resp = requests.get(f"http://{CAM_IP}:{CAM_PORT}/status", timeout=3)
        resp.raise_for_status()
        return {
            "online": True,
            "cam_ip": CAM_IP,
            "cam_port": CAM_PORT,
            "cam_stream_port": CAM_STREAM_PORT,
        }
    except requests.exceptions.RequestException:
        return {
            "online": False,
            "cam_ip": CAM_IP,
            "cam_port": CAM_PORT,
            "cam_stream_port": CAM_STREAM_PORT,
        }
