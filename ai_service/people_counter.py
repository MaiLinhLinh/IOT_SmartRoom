"""
people_counter.py — Chức năng 1: dùng YOLO đếm số người trong phòng.

Luồng mỗi chu kỳ (mặc định 2 giây):
    lấy 1 khung hình (ESP32-CAM /capture, backend snapshot, webcam hoặc file)
    → YOLO phát hiện class 0 = "person"
    → làm mượt bằng trung vị N lần đếm gần nhất (chống nhấp nháy khi YOLO bỏ sót 1 khung)
    → POST /api/people/count khi số người thay đổi, hoặc định kỳ (heartbeat)
      để backend luôn có people_updated_at mới.
"""
import logging
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import requests

from backend_client import BackendClient, BackendError
from config import Settings

logger = logging.getLogger("counter")

PERSON_CLASS_ID = 0  # COCO: 0 = person
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


# ======================================================================
# Nguồn ảnh
# ======================================================================
class FrameSource:
    """Trả về ảnh BGR (numpy) từ nhiều loại nguồn khác nhau."""

    def __init__(self, spec: str, settings: Settings, backend: BackendClient):
        self.spec = spec.strip()
        self.settings = settings
        self.backend = backend
        self.session = requests.Session()
        self._cap: Optional[cv2.VideoCapture] = None
        self._static: Optional[np.ndarray] = None

        lower = self.spec.lower()
        if lower.startswith("webcam"):
            index = int(lower.split(":", 1)[1]) if ":" in lower else 0
            self._cap = cv2.VideoCapture(index)
            self.kind = f"webcam {index}"
        elif lower in ("esp32", "backend") or lower.startswith(("http://", "https://")):
            self.kind = lower if lower in ("esp32", "backend") else "url"
        else:
            path = Path(self.spec)
            if not path.exists():
                raise FileNotFoundError(f"Không tìm thấy nguồn ảnh: {self.spec}")
            if path.suffix.lower() in IMAGE_EXTS:
                self._static = cv2.imread(str(path))
                self.kind = "image file"
            else:
                self._cap = cv2.VideoCapture(str(path))
                self.kind = "video file"

    def describe(self) -> str:
        if self.kind == "esp32":
            return f"ESP32-CAM {self.settings.esp32_capture_url}"
        if self.kind == "backend":
            return f"backend {self.backend.base_url}/api/camera/snapshot"
        return f"{self.kind} ({self.spec})"

    @staticmethod
    def _decode(data: bytes) -> np.ndarray:
        frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Dữ liệu nhận về không phải ảnh JPEG hợp lệ")
        return frame

    def read(self) -> np.ndarray:
        if self._static is not None:
            return self._static.copy()

        if self._cap is not None:
            ok, frame = self._cap.read()
            if not ok and self.kind == "video file":  # hết video → quay lại đầu
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = self._cap.read()
            if not ok:
                raise RuntimeError(f"Không đọc được khung hình từ {self.describe()}")
            return frame

        if self.kind == "backend":
            return self._decode(self.backend.get_snapshot())

        url = self.settings.esp32_capture_url if self.kind == "esp32" else self.spec
        resp = self.session.get(url, timeout=self.settings.request_timeout)
        resp.raise_for_status()
        return self._decode(resp.content)

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()


# ======================================================================
# YOLO
# ======================================================================
class PeopleDetector:
    """Bọc model YOLO (Ultralytics). Model chỉ nạp 1 lần."""

    def __init__(self, model_path: str, conf: float = 0.4, imgsz: int = 640, device: str = ""):
        from ultralytics import YOLO  # import trễ: các phần khác không phụ thuộc torch

        logger.info("Đang nạp model YOLO: %s", model_path)
        self.model = YOLO(model_path)
        self.conf = conf
        self.imgsz = imgsz
        self.device = device or None

    def detect(self, frame: np.ndarray):
        """Trả về (số người, kết quả Ultralytics)."""
        results = self.model.predict(
            frame,
            classes=[PERSON_CLASS_ID],
            conf=self.conf,
            imgsz=self.imgsz,
            device=self.device,
            verbose=False,
        )
        result = results[0]
        return len(result.boxes), result


class CountSmoother:
    """Trung vị của N lần đếm gần nhất."""

    def __init__(self, window: int = 5):
        self.values: deque[int] = deque(maxlen=max(1, window))

    def add(self, value: int) -> int:
        self.values.append(int(value))
        ordered = sorted(self.values)
        return ordered[len(ordered) // 2]

    def reset(self) -> None:
        self.values.clear()


# ======================================================================
# Vòng lặp đếm người
# ======================================================================
def run_counter_loop(
    settings: Settings,
    backend: BackendClient,
    stop_event: threading.Event,
    source_spec: Optional[str] = None,
    show: bool = False,
    once: bool = False,
    dry_run: bool = False,
) -> Optional[int]:
    source = FrameSource(source_spec or settings.camera_source, settings, backend)
    detector = PeopleDetector(settings.yolo_model, settings.yolo_conf, settings.yolo_imgsz, settings.yolo_device)
    smoother = CountSmoother(settings.smooth_window)

    logger.info("Nguồn ảnh: %s | chu kỳ %.1fs | làm mượt %d khung",
                source.describe(), settings.count_interval, settings.smooth_window)

    last_pushed: Optional[int] = None
    last_push_time = 0.0
    consecutive_errors = 0
    smoothed: Optional[int] = None

    try:
        while not stop_event.is_set():
            started = time.monotonic()
            try:
                frame = source.read()
                raw_count, result = detector.detect(frame)
                smoothed = smoother.add(raw_count)
                consecutive_errors = 0

                elapsed_ms = (time.monotonic() - started) * 1000
                logger.info("YOLO: thô=%d → làm mượt=%d (%.0f ms)", raw_count, smoothed, elapsed_ms)

                if settings.debug_frame_path or show:
                    annotated = result.plot()
                    cv2.putText(annotated, f"People: {smoothed}", (10, 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
                    if settings.debug_frame_path:
                        Path(settings.debug_frame_path).parent.mkdir(parents=True, exist_ok=True)
                        cv2.imwrite(settings.debug_frame_path, annotated)
                    if show:
                        cv2.imshow("AIoT - YOLO people counter", annotated)
                        if cv2.waitKey(1) & 0xFF == ord("q"):
                            stop_event.set()

                now = time.monotonic()
                need_push = smoothed != last_pushed or (now - last_push_time) >= settings.people_heartbeat
                if need_push and not dry_run:
                    try:
                        backend.push_people_count(smoothed, source="YOLO")
                        if smoothed != last_pushed:
                            logger.info("→ Đã gửi backend: people_count=%d", smoothed)
                        last_pushed, last_push_time = smoothed, now
                    except BackendError as exc:
                        logger.warning("Không gửi được số người: %s", exc)

            except Exception as exc:  # camera mất kết nối, ảnh hỏng...
                consecutive_errors += 1
                logger.warning("Lỗi đếm người (%d lần liên tiếp): %s", consecutive_errors, exc)
                if consecutive_errors >= 3:
                    # Không gửi số cũ: để people_updated_at cũ đi, bộ quyết định sẽ biết dữ liệu không tin cậy
                    smoother.reset()

            if once:
                break
            wait = settings.count_interval - (time.monotonic() - started)
            if consecutive_errors:
                wait = max(wait, min(30, settings.count_interval * 2 ** min(consecutive_errors, 4)))
            stop_event.wait(max(0.0, wait))
    finally:
        source.close()
        if show:
            cv2.destroyAllWindows()

    return smoothed
