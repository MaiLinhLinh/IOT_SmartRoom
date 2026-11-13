"""
prompts.py — Prompt và schema JSON gửi cho Gemini.
"""
from datetime import datetime
from typing import Optional

SYSTEM_PROMPT_TEMPLATE = """Bạn là bộ điều khiển khí hậu của một phòng học/phòng làm việc thông minh (hệ thống IoT).
Nhiệm vụ: dựa vào số người trong phòng, nhiệt độ, độ ẩm và trạng thái thiết bị hiện tại, chọn cấu hình quạt và điều hòa
sao cho người trong phòng thoải mái, đồng thời tiết kiệm điện.

THIẾT BỊ
- Quạt: fan_level = 0 (tắt), 1, 2, 3 (mạnh nhất). Quạt tốn ít điện hơn điều hòa rất nhiều.
- Điều hòa: ac_on = true/false; ac_temp = nhiệt độ đặt, số nguyên từ {ac_min} đến {ac_max} °C.

NGUYÊN TẮC
1. Không có người và dữ liệu số người còn mới → tắt quạt và điều hòa.
2. Vùng thoải mái mục tiêu: nhiệt độ 24–27°C, độ ẩm 40–70%.
3. Ưu tiên quạt trước. Chỉ bật điều hòa khi nóng rõ rệt (khoảng từ 28°C), hoặc vừa ấm vừa ẩm
   (từ 27°C và độ ẩm từ 70%), hoặc phòng đông người làm nhiệt tăng nhanh.
4. Càng đông người càng sinh nhiều nhiệt: cân nhắc tăng 1 mức quạt hoặc hạ nhiệt độ đặt 1°C.
5. Không đặt điều hòa quá lạnh: thường 25–27°C; chỉ xuống 24°C khi rất nóng (từ 31°C) và đông người.
6. Tránh bật/tắt liên tục: nếu trạng thái hiện tại vẫn hợp lý và môi trường chỉ thay đổi nhỏ thì GIỮ NGUYÊN.
7. Nếu số người KHÔNG RÕ (dữ liệu camera đã cũ) → giữ ổn định, không tắt các thiết bị đang chạy chỉ vì thiếu dữ liệu.
8. Khi điều hòa tắt, vẫn trả ac_temp (giữ nguyên giá trị đặt hiện tại).

ĐẦU RA
Chỉ trả về MỘT object JSON, không giải thích thêm:
{{"fan_level": <0-3>, "ac_on": <true|false>, "ac_temp": <{ac_min}-{ac_max}>, "ai_reason": "<lý do>"}}
ai_reason: tiếng Việt có dấu, 1–2 câu, tối đa 200 ký tự, nêu số người, nhiệt độ/độ ẩm và hành động đã chọn.

VÍ DỤ
- 0 người, 30°C, 65% → {{"fan_level": 0, "ac_on": false, "ac_temp": 26, "ai_reason": "Phòng trống, 30°C → tắt quạt và điều hòa để tiết kiệm điện."}}
- 2 người, 26.5°C, 60% → {{"fan_level": 1, "ac_on": false, "ac_temp": 26, "ai_reason": "2 người, 26.5°C dễ chịu → chỉ cần quạt mức 1."}}
- 5 người, 30°C, 75% → {{"fan_level": 2, "ac_on": true, "ac_temp": 25, "ai_reason": "5 người, 30°C, ẩm 75% khá oi → bật điều hòa 25°C kèm quạt mức 2."}}
"""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "fan_level": {"type": "INTEGER", "description": "Mức quạt 0-3 (0 = tắt)"},
        "ac_on": {"type": "BOOLEAN", "description": "Bật điều hòa hay không"},
        "ac_temp": {"type": "INTEGER", "description": "Nhiệt độ đặt điều hòa (°C)"},
        "ai_reason": {"type": "STRING", "description": "Lý do ngắn gọn bằng tiếng Việt"},
    },
    "required": ["fan_level", "ac_on", "ac_temp", "ai_reason"],
    "propertyOrdering": ["fan_level", "ac_on", "ac_temp", "ai_reason"],
}


def build_system_prompt(ac_min: int, ac_max: int) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(ac_min=ac_min, ac_max=ac_max)


def _age_text(seconds: Optional[float]) -> str:
    if seconds is None:
        return "không rõ thời điểm"
    if seconds < 90:
        return f"{seconds:.0f} giây trước"
    return f"{seconds / 60:.0f} phút trước"


def build_user_prompt(
    *,
    people_count: Optional[int],
    people_age: Optional[float],
    people_source: str,
    temperature: float,
    humidity: Optional[float],
    sensor_age: Optional[float],
    fan_level: int,
    ac_on: bool,
    ac_temp: int,
    last_reason: Optional[str],
    now: Optional[datetime] = None,
) -> str:
    now = now or datetime.now()
    if people_count is None:
        people_line = f"KHÔNG RÕ (dữ liệu camera đã cũ, cập nhật {_age_text(people_age)})"
    else:
        people_line = f"{people_count} người (nguồn {people_source}, cập nhật {_age_text(people_age)})"

    humidity_line = f"{humidity:.0f}%" if humidity is not None else "không có dữ liệu"
    ac_line = f"BẬT, đặt {ac_temp}°C" if ac_on else f"TẮT (giá trị đặt {ac_temp}°C)"
    fan_line = "TẮT" if fan_level == 0 else f"mức {fan_level}"

    lines = [
        "DỮ LIỆU HIỆN TẠI",
        f"- Thời gian: {now:%H:%M} ngày {now:%d/%m/%Y}",
        f"- Số người trong phòng: {people_line}",
        f"- Nhiệt độ phòng (DHT11): {temperature:.1f}°C",
        f"- Độ ẩm phòng (DHT11): {humidity_line} (cảm biến cập nhật {_age_text(sensor_age)})",
        "",
        "TRẠNG THÁI THIẾT BỊ HIỆN TẠI",
        f"- Quạt: {fan_line}",
        f"- Điều hòa: {ac_line}",
    ]
    if last_reason:
        lines.append(f"- Lý do của quyết định trước: {last_reason}")
    lines += ["", "Hãy đưa ra cấu hình quạt và điều hòa phù hợp (JSON)."]
    return "\n".join(lines)
