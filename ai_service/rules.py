"""
rules.py — Luật ngưỡng dự phòng khi Gemini lỗi / hết quota / trả sai định dạng.

Dựa trên bảng của backend (services/ai_engine.py) nhưng có thêm:
  - nhiệt độ "cảm nhận" tăng khi độ ẩm cao và khi đông người;
  - số người không rõ (dữ liệu cũ) → giữ nguyên trạng thái, không tắt bừa.
"""
from typing import Optional


def effective_temperature(temperature: float, humidity: Optional[float], people_count: int) -> float:
    t = temperature
    if humidity is not None:
        if humidity >= 80:
            t += 1.5
        elif humidity >= 70:
            t += 1.0
        elif humidity >= 60:
            t += 0.5
    if people_count >= 6:
        t += 1.0
    elif people_count >= 3:
        t += 0.5
    return t


def rule_decision(
    temperature: float,
    humidity: Optional[float],
    people_count: Optional[int],
    current: dict,
) -> Optional[dict]:
    """
    Trả về dict {fan_level, ac_on, ac_temp, ai_reason} hoặc None nếu nên giữ nguyên.
    people_count=None nghĩa là không rõ số người (dữ liệu YOLO đã cũ).
    """
    if people_count is None:
        return None

    keep_temp = int(current.get("ac_temp") or 26)

    if people_count == 0:
        return {
            "fan_level": 0, "ac_on": False, "ac_temp": keep_temp,
            "ai_reason": f"Không có người trong phòng, nhiệt độ {temperature:.1f}°C → tắt quạt và điều hòa để tiết kiệm điện.",
        }

    t_eff = effective_temperature(temperature, humidity, people_count)
    hum_text = f", độ ẩm {humidity:.0f}%" if humidity is not None else ""

    if t_eff < 25:
        fan, ac_on, ac_temp, action = 0, False, keep_temp, "mát, tắt quạt và điều hòa"
    elif t_eff < 28:
        fan, ac_on, ac_temp, action = 1, False, keep_temp, "dễ chịu, bật quạt mức 1"
    elif t_eff < 31:
        fan, ac_on, ac_temp, action = 2, True, 26, "khá nóng, quạt mức 2 và điều hòa 26°C"
    else:
        fan, ac_on, ac_temp, action = 3, True, 25, "rất nóng, quạt mức 3 và điều hòa 25°C"

    return {
        "fan_level": fan, "ac_on": ac_on, "ac_temp": ac_temp,
        "ai_reason": f"{people_count} người, {temperature:.1f}°C{hum_text} (cảm nhận ~{t_eff:.1f}°C) → {action}.",
    }
