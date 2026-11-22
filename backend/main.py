"""
main.py v3 — Entry point FastAPI
Chạy: uvicorn main:app --host 0.0.0.0 --port 8006 --reload
Docs: http://localhost:8006/docs
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import engine, Base, SessionLocal
from models import DeviceInfo, DeviceState
from routers import auth, sensor, device, device_info, energy, logs, camera, control_logs, people, ai
from services.ai_engine import ai_control_loop
from services.energy_service import seed_device_info

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt = "%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


def _seed_device_state() -> None:
    """Đảm bảo luôn có row device_state(id=1)."""
    db = SessionLocal()
    try:
        if not db.query(DeviceState).filter(DeviceState.id == 1).first():
            db.add(DeviceState(id=1))
            db.commit()
            logger.info("Đã tạo device_state(id=1)")
    finally:
        db.close()


def _seed_device_info() -> None:
    """Đảm bảo device_info có đủ 4 thiết bị (fan1, fan2, fan3, ac)."""
    db = SessionLocal()
    try:
        seed_device_info(db)
        logger.info("device_info đã sẵn sàng (4 thiết bị)")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== AIoT Backend v5 khởi động ===")
    Base.metadata.create_all(bind=engine)
    _seed_device_state()
    _seed_device_info()
    logger.info("Database tables checked/created")

    ai_enabled = os.getenv("AI_ENGINE_ENABLED", "true").lower() in (
        "true", "1", "yes", "on"
    )
    ai_task = asyncio.create_task(ai_control_loop()) if ai_enabled else None
    logger.info(
        "AI Engine background loop %s",
        "started" if ai_enabled else "DISABLED (dùng AI bên ngoài qua /api/ai/command)",
    )
    yield
    if ai_task:
        ai_task.cancel()
    logger.info("=== AIoT Backend tắt ===")


app = FastAPI(
    title       = "AIoT Smart Room API v5",
    description = """
## Hệ thống điều khiển phòng thông minh AIoT - v5

### 6 trang frontend:
1. **Login** — POST /auth/login
2. **Dashboard** — GET /api/sensor/latest + /api/device/state + POST /api/device/command + PATCH /api/device/mode + GET /api/energy/summary
3. **Camera** — GET /api/camera/stream-url + POST /auth/stream-token + GET /api/camera/stream?token= + GET /api/people/latest
4. **Sensor Logs** — GET /api/logs
5. **Profile** — GET/PUT /auth/profile, PUT /auth/change-password
6. **Control History** — GET /api/control-logs
+ **Biểu đồ điện năng** — GET /api/energy/daily?days=7
+ **Cấu hình thiết bị (hãng/công suất)** — GET/PUT /api/devices/info

### Module YOLO chạy riêng:
POST /api/people/count (header X-API-Key nếu có) → backend lưu số người.

### AI service bên ngoài:
GET /api/ai/context + POST /api/ai/command (header X-API-Key nếu có).
Đặt AI_ENGINE_ENABLED=false để tắt AI nội bộ.
    """,
    version  = "5.0.0",
    lifespan = lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# Routers
app.include_router(auth.router)
app.include_router(sensor.router)
app.include_router(device.router)
app.include_router(device_info.router)
app.include_router(energy.router)
app.include_router(logs.router)
app.include_router(camera.router)
app.include_router(control_logs.router)
app.include_router(people.router)
app.include_router(ai.router)


@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "message": "AIoT Smart Room Backend v5", "docs": "/docs"}


@app.get("/health", tags=["Health"])
def health():
    return {"status": "healthy"}
