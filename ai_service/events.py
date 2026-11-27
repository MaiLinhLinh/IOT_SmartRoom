"""
events.py — Sự kiện có cấu trúc cho bảng điều khiển (gui.py).

Khi chạy từ gui.py (biến môi trường GUI_EVENTS=1), main.py / simulate.py in thêm các dòng
    @@EVENT {"type": "...", ...}
để giao diện vẽ trạng thái (bước kịch bản, quyết định, cảnh báo) thay vì chỉ hiện log chữ.
Chạy bằng dòng lệnh bình thường thì không in gì thêm.
"""
import json
import os
import sys
import threading
import time

PREFIX = "@@EVENT "
_lock = threading.Lock()
_active: dict = {}   # key → (level, title, message): cảnh báo đang hiện, để không gửi lặp


def enabled() -> bool:
    return os.environ.get("GUI_EVENTS") == "1"


def emit(type_: str, **data) -> None:
    if not enabled():
        return
    payload = {"type": type_, "ts": time.time(), **data}
    line = PREFIX + json.dumps(payload, ensure_ascii=False, default=str)
    with _lock:
        try:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except (OSError, ValueError):
            pass


def alert(key: str, level: str, title: str, message: str = "", hint: str = "") -> None:
    """Cảnh báo hiển thị thành thẻ trên giao diện. key giống nhau → gộp làm một (không lặp)."""
    if not enabled():
        return
    sig = (level, title, message)
    if _active.get(key) == sig:
        return
    _active[key] = sig
    emit("alert", key=key, level=level, title=title, message=message, hint=hint)


def clear_alert(key: str) -> None:
    if _active.pop(key, None) is not None:
        emit("alert_clear", key=key)
