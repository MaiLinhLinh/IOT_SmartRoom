"""
services/yolo_service.py — OPTIONAL: runner YOLO cục bộ (không bắt buộc).

AI engine của backend KHÔNG còn gọi file này. Module YOLO chạy riêng (bạn của bạn)
chỉ cần POST số người lên backend:

    POST http://<BACKEND_IP>:8006/api/people/count
    Header: X-API-Key: <PEOPLE_API_KEY>   (nếu .env có đặt)
    Body:   {"count": 3, "source": "YOLO"}

Nếu muốn chạy YOLO ngay trên máy backend, dùng script này:
    pip install ultralytics opencv-python numpy requests
    python -m services.yolo_service
"""
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

CAM_IP      = os.getenv("CAM_IP", "192.168.1.100")
CAM_PORT    = os.getenv("CAM_PORT", "80")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8006")
PEOPLE_KEY  = os.getenv("PEOPLE_API_KEY", "")
INTERVAL    = float(os.getenv("YOLO_INTERVAL_SECONDS", "2"))


def count_people_from_cam() -> int:
    """Chụp 1 frame ESP32-CAM và đếm class 'person' bằng YOLOv8."""
    from ultralytics import YOLO  # import trễ để backend không phụ thuộc ultralytics

    model = YOLO("yolov8n.pt")
    resp = requests.get(f"http://{CAM_IP}:{CAM_PORT}/capture", timeout=5)
    resp.raise_for_status()

    results = model.predict(resp.content, classes=[0], verbose=False)
    return len(results[0].boxes)


def push_people_count(count: int) -> None:
    headers = {"X-API-Key": PEOPLE_KEY} if PEOPLE_KEY else {}
    requests.post(
        f"{BACKEND_URL}/api/people/count",
        json={"count": count, "source": "YOLO"},
        headers=headers,
        timeout=5,
    )


if __name__ == "__main__":
    print(f"[YOLO] Camera={CAM_IP}:{CAM_PORT} Backend={BACKEND_URL}")
    while True:
        try:
            count = count_people_from_cam()
            push_people_count(count)
            print(f"[YOLO] people_count={count}")
        except Exception as exc:
            print(f"[YOLO] Lỗi: {exc}")
        time.sleep(INTERVAL)
