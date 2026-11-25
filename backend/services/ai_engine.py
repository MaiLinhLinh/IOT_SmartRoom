"""
services/ai_engine.py — AI tự động điều khiển (chế độ AUTO) — v5

Nguồn số người: module YOLO chạy riêng đẩy lên POST /api/people/count
→ backend lưu device_state.people_count → AI engine đọc lại (không tự gọi CAM).

Bảng quyết định (3 quạt độc lập, ưu tiên tiết kiệm điện — quạt 45W, AC 900W):
  Không có người : tắt hết
  T < 25         : tắt hết (mát rồi)
  25 <= T < 28   : fan1                                (45W)
  28 <= T < 31   : fan1 + fan2, AC ON (T_set=26)       (90W + 900W)
  T >= 31        : fan1 + fan2 + fan3, AC ON (T_set=24) (135W + 900W)

Chỉ chạy khi mode = "AUTO". Mọi thay đổi đều ghi control_logs + ai_reason.
"""
import asyncio
import logging

from services.device_service import apply_command, get_state

logger = logging.getLogger("ai_engine")


def make_ai_decision(temperature: float, humidity: float, people_count: int) -> dict:
    """Hàm quyết định thuần túy — dễ unit test / thay bằng model ML sau."""
    fans = {
        "fan1": False, "fan2": False, "fan3": False,
        "ac_on": False, "ac_temp": 26,
    }

    if people_count == 0:
        return fans
    if temperature < 25:
        return fans
    elif temperature < 28:
        fans["fan1"] = True
    elif temperature < 31:
        fans["fan1"] = True
        fans["fan2"] = True
        fans["ac_on"] = True
    else:
        fans["fan1"] = True
        fans["fan2"] = True
        fans["fan3"] = True
        fans["ac_on"] = True
        fans["ac_temp"] = 24
    return fans


def build_ai_reason(temperature: float, people_count: int, decision: dict) -> str:
    """Câu giải thích hiển thị trên Dashboard (card chấm hồng)."""
    if people_count == 0:
        return (
            f"Không phát hiện người trong phòng, nhiệt độ {temperature:.1f}°C "
            f"→ Tắt hết quạt và điều hòa để tiết kiệm điện"
        )

    if temperature >= 31:
        situation = "trời rất nóng"
    elif temperature >= 28:
        situation = "khá nóng"
    elif temperature >= 25:
        situation = "dễ chịu"
    else:
        situation = "mát mẻ"

    active = [i for i in (1, 2, 3) if decision[f"fan{i}"]]
    if active:
        fan_text = "Bật quạt " + ", ".join(str(i) for i in active)
    else:
        fan_text = "Tắt hết quạt"

    ac_text = (
        f"bật điều hòa {decision['ac_temp']}°C"
        if decision["ac_on"]
        else "tắt điều hòa"
    )

    return (
        f"Phát hiện {people_count} người, nhiệt độ {temperature:.1f}°C {situation} "
        f"→ {fan_text}, {ac_text}"
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
                fan1=decision["fan1"],
                fan2=decision["fan2"],
                fan3=decision["fan3"],
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
