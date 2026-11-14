"""
services/device_service.py — Logic dùng chung cho Manual (Flutter) và AI.

Mọi thay đổi thiết bị đều đi qua apply_command():
- cập nhật device_state
- tự ghi control_logs khi có thay đổi thật
- lưu kèm bối cảnh cảm biến (nhiệt độ, độ ẩm, số người)
"""
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from models import ControlLog, DeviceState, SensorLog


def get_state(db: Session) -> DeviceState:
    """Lấy device_state (tự tạo row id=1 nếu chưa có)."""
    state = db.query(DeviceState).filter(DeviceState.id == 1).first()
    if not state:
        state = DeviceState(id=1)
        db.add(state)
        db.commit()
        db.refresh(state)
    return state


def build_description(
    *,
    fan1: bool,
    fan2: bool,
    fan3: bool,
    ac_on: bool,
    ac_temp: int,
    mode: str,
    prev: DeviceState,
) -> Tuple[str, bool]:
    """Trả về (câu mô tả tiếng Việt, có_thay_đổi_không)."""
    parts = []

    fan_news = (fan1, fan2, fan3)
    fan_prevs = (prev.fan1, prev.fan2, prev.fan3)
    for i, (new, old) in enumerate(zip(fan_news, fan_prevs), start=1):
        if new != old:
            parts.append(f"Bật quạt {i}" if new else f"Tắt quạt {i}")

    if ac_on != prev.ac_on:
        parts.append(
            f"Bật điều hòa {ac_temp}°C" if ac_on else "Tắt điều hòa"
        )
    elif ac_on and ac_temp != prev.ac_temp:
        parts.append(f"Chỉnh điều hòa {ac_temp}°C")

    if mode != prev.mode:
        parts.append(
            "Chuyển sang chế độ TỰ ĐỘNG" if mode == "AUTO" else "Chuyển sang chế độ THỦ CÔNG"
        )

    if not parts:
        return "Không thay đổi", False

    return ", ".join(parts), True


def apply_command(
    db: Session,
    *,
    fan1: bool,
    fan2: bool,
    fan3: bool,
    ac_on: bool,
    ac_temp: int,
    mode: str,
    actor: str,
    actor_type: str,                       # "USER" | "AI"
    actor_user_id: Optional[int] = None,
    source: str = "MANUAL",                # "MANUAL" | "AI"
    ai_reason: Optional[str] = None,
) -> Tuple[DeviceState, str, bool]:
    """
    Áp dụng 1 lệnh điều khiển và ghi lịch sử.
    Trả về (state, description, changed).
    """
    state = get_state(db)

    prev_fan1 = state.fan1
    prev_fan2 = state.fan2
    prev_fan3 = state.fan3
    prev_ac_on = state.ac_on
    prev_ac_temp = state.ac_temp

    description, changed = build_description(
        fan1=fan1,
        fan2=fan2,
        fan3=fan3,
        ac_on=ac_on,
        ac_temp=ac_temp,
        mode=mode,
        prev=state,
    )

    state.fan1 = fan1
    state.fan2 = fan2
    state.fan3 = fan3
    state.ac_on = ac_on
    state.ac_temp = ac_temp
    state.mode = mode
    state.updated_at = datetime.now()   # giờ local, đồng bộ với GETDATE() của SQL Server

    if source == "AI":
        state.ai_reason = ai_reason
    elif changed:
        # Người dùng vừa can thiệp -> xoá lý do AI cũ để Dashboard không hiển thị sai
        state.ai_reason = None

    if changed:
        latest = db.query(SensorLog).order_by(SensorLog.id.desc()).first()
        log = ControlLog(
            actor=actor,
            actor_type=actor_type,
            actor_user_id=actor_user_id,
            source=source,
            description=description,
            fan1=fan1,
            fan2=fan2,
            fan3=fan3,
            ac_on=ac_on,
            ac_temp=ac_temp,
            mode=mode,
            fan1_prev=prev_fan1,
            fan2_prev=prev_fan2,
            fan3_prev=prev_fan3,
            ac_on_prev=prev_ac_on,
            ac_temp_prev=prev_ac_temp,
            ai_reason=ai_reason,
            temperature=latest.temperature if latest else None,
            humidity=latest.humidity if latest else None,
            people_count=state.people_count,
        )
        db.add(log)

    db.commit()
    db.refresh(state)
    return state, description, changed
