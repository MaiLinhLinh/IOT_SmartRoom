"""
routers/device.py v3
GET   /api/device/state    ← ESP32 (poll 2s) + Flutter
POST  /api/device/command  ← Flutter (Manual) — bắt buộc JWT
PATCH /api/device/mode     ← Flutter đổi AUTO/MANUAL — bắt buộc JWT
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import User
from routers.auth import get_current_user
from schemas import DeviceStateOut, DeviceCommandIn, ModeUpdateIn
from services.device_service import apply_command, get_state

router = APIRouter(prefix="/api/device", tags=["Device"])


@router.get("/state", response_model=DeviceStateOut, summary="Lấy trạng thái thiết bị")
def read_state(db: Session = Depends(get_db)):
    """
    ESP32 poll mỗi 2 giây + Flutter đọc để đồng bộ UI.
    Trả kèm ai_reason, people_count, people_source, people_updated_at.
    """
    return get_state(db)


@router.post("/command", response_model=DeviceStateOut, summary="Gửi lệnh điều khiển (Manual)")
def send_command(
    body:         DeviceCommandIn,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
):
    """
    Chỉ dùng khi MANUAL. `actor` được suy ra từ JWT — không nhận từ body.
    Nếu đang AUTO, phải PATCH /api/device/mode sang MANUAL trước.
    """
    state = get_state(db)

    if state.mode == "AUTO":
        raise HTTPException(
            status_code=409,
            detail="Đang ở chế độ AUTO. Hãy chuyển sang MANUAL trước khi điều khiển thủ công.",
        )

    state, _, _ = apply_command(
        db,
        fan1=body.fan1,
        fan2=body.fan2,
        fan3=body.fan3,
        ac_on=body.ac_on,
        ac_temp=body.ac_temp,
        mode="MANUAL",
        actor=current_user.username,
        actor_type="USER",
        actor_user_id=current_user.id,
        source="MANUAL",
        ai_reason=None,
    )
    return state


@router.patch("/mode", response_model=DeviceStateOut, summary="Đổi chế độ AUTO/MANUAL")
def change_mode(
    body:         ModeUpdateIn,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
):
    """Chỉ đổi mode, giữ nguyên mức quạt/điều hòa hiện tại."""
    state = get_state(db)

    state, _, _ = apply_command(
        db,
        fan1=state.fan1,
        fan2=state.fan2,
        fan3=state.fan3,
        ac_on=state.ac_on,
        ac_temp=state.ac_temp,
        mode=body.mode,
        actor=current_user.username,
        actor_type="USER",
        actor_user_id=current_user.id,
        source="MANUAL",
        ai_reason=None,
    )
    return state
