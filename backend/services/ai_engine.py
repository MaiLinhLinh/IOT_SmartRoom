"""
services/ai_engine.py — AI tự động điều khiển (chế độ AUTO)

Nguồn số người: module YOLO chạy riêng đẩy lên POST /api/people/count
→ backend lưu device_state.people_count → AI engine đọc lại (không tự gọi CAM).

Logic bảng quyết định:
  Không có người : fan=0, AC=OFF
  T < 25         : fan=0, AC=OFF
  25 <= T < 28   : fan=1, AC=OFF
  28 <= T < 31   : fan=2, AC=ON  (T_set=26)
  T >= 31        : fan=3, AC=ON  (T_set=24)

Chỉ chạy khi mode = "AUTO". Mọi thay đổi đều ghi control_logs + ai_reason.
"""
import asyncio
import logging

from services.device_service import apply_command, get_state

logger = logging.getLogger("ai_engine")


def make_ai_decision(temperature: float, humidity: float, people_count: int) -> dict:
    """Hàm quyết định thuần túy — dễ unit test / thay bằng model ML sau."""
    if people_count == 0:
        return {"fan_level": 0, "ac_on": False, "ac_temp": 26}

    if temperature < 25:
        return {"fan_level": 0, "ac_on": False, "ac_temp": 26}
    elif temperature < 28:
        return {"fan_level": 1, "ac_on": False, "ac_temp": 26}
    elif temperature < 31:
        return {"fan_level": 2, "ac_on": True,  "ac_temp": 26}
    else:
        return {"fan_level": 3, "ac_on": True,  "ac_temp": 24}


def build_ai_reason(temperature: float, people_count: int, decision: dict) -> str:
    """Câu giải thích hiển thị trên Dashboard (card chấm hồng)."""
    if people_count == 0:
        return (
            f"Không phát hiện người trong phòng, nhiệt độ {temperature:.1f}°C "
            f"→ Tắt quạt và điều hòa để tiết kiệm điện"
        )

    if temperature >= 31:
        situation = "trời rất nóng"
    elif temperature >= 28:
        situation = "khá nóng"
    elif temperature >= 25:
        situation = "dễ chịu"
    else:
        situation = "mát mẻ"

    fan_text = "tắt quạt" if decision["fan_level"] == 0 else f"quạt mức {decision['fan_level']}"
    ac_text = (
        f"bật điều hòa {decision['ac_temp']}°C"
        if decision["ac_on"]
        else "tắt điều hòa"
    )

    return (
        f"Phát hiện {people_count} người, nhiệt độ {temperature:.1f}°C {situation} "
        f"→ {fan_text[0].upper()}{fan_text[1:]}, {ac_text}"
    )


async def ai_control_loop():
    """Background task chạy mỗi 10 giây."""
    from database import SessionLocal
    from models import SensorLog

    logger.info("[AI Engine] Background loop started")

    while True:
        await asyncio.sleep(10)
        db = SessionLocal()
        try:
            state = get_state(db)
            if state.mode != "AUTO":
                continue

            latest = db.query(SensorLog).order_by(SensorLog.id.desc()).first()
            if not latest:
                continue

            temperature = latest.temperature if latest.temperature is not None else 25.0
            people = state.people_count or 0

            decision = make_ai_decision(temperature, latest.humidity or 60.0, people)
            reason = build_ai_reason(temperature, people, decision)

            _, description, changed = apply_command(
                db,
                fan_level=decision["fan_level"],
                ac_on=decision["ac_on"],
                ac_temp=decision["ac_temp"],
                mode="AUTO",
                actor="AI",
                actor_type="AI",
                actor_user_id=None,
                source="AI",
                ai_reason=reason,
            )

            if changed:
                logger.info(
                    f"[AI] T={temperature}°C People={people} → {description} | {reason}"
                )
        except Exception as e:
            logger.error(f"[AI Engine] Lỗi: {e}")
        finally:
            db.close()
