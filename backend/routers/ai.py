"""
routers/ai.py — Cổng cho AI service bên ngoài (YOLO/model riêng).

AI KHÔNG cần biết IP ESP32. Luồng:
    AI → POST /api/ai/command → device_state
    ESP32 poll GET /api/device/state (2s) → điều khiển LED/LCD

Nếu .env có AI_API_KEY thì bắt buộc header X-API-Key.
Khi dùng AI bên ngoài, đặt AI_ENGINE_ENABLED=false để AI engine nội bộ
không ghi đè quyết định.
"""
import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import SensorLog
from schemas import AIDecisionIn, DeviceStateOut
from services.device_service import apply_command, get_state

load_dotenv()

AI_API_KEY = os.getenv("AI_API_KEY", "")

router = APIRouter(prefix="/api/ai", tags=["AI External"])


def verify_ai_key(x_api_key: Optional[str] = Header(default=None)):
    if AI_API_KEY and x_api_key != AI_API_KEY:
        raise HTTPException(status_code=401, detail="X-API-Key không hợp lệ")


@router.get("/context", summary="Dữ liệu để AI bên ngoài ra quyết định")
def get_ai_context(db: Session = Depends(get_db)):
    """Trả trạng thái + cảm biến + số người trong 1 lần gọi."""
    state = get_state(db)
    latest = db.query(SensorLog).order_by(SensorLog.id.desc()).first()
    return {
        "mode": state.mode,
        "fan_level": state.fan_level,
        "ac_on": state.ac_on,
        "ac_temp": state.ac_temp,
        "people_count": state.people_count,
        "people_source": state.people_source,
        "people_updated_at": state.people_updated_at,
        "temperature": latest.temperature if latest else None,
        "humidity": latest.humidity if latest else None,
        "sensor_timestamp": latest.timestamp if latest else None,
        "ai_reason": state.ai_reason,
    }


@router.post(
    "/command",
    response_model=DeviceStateOut,
    summary="AI bên ngoài gửi lệnh điều khiển",
    dependencies=[Depends(verify_ai_key)],
)
def ai_command(body: AIDecisionIn, db: Session = Depends(get_db)):
    """
    Chỉ hoạt động khi device ở chế độ AUTO.
    Ghi control_logs với actor='AI', source='AI', kèm ai_reason.
    """
    state = get_state(db)
    if state.mode != "AUTO":
        raise HTTPException(
            status_code=409,
            detail="Thiết bị đang MANUAL. Chuyển sang AUTO trước khi AI điều khiển.",
        )

    state, _, _ = apply_command(
        db,
        fan_level=body.fan_level,
        ac_on=body.ac_on,
        ac_temp=body.ac_temp,
        mode="AUTO",
        actor="AI",
        actor_type="AI",
        actor_user_id=None,
        source="AI",
        ai_reason=body.ai_reason,
    )
    return state
