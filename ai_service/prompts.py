"""
prompts.py — Prompt và schema JSON gửi cho Gemini (backend v4: 3 quạt độc lập + 1 điều hòa).

Gemini không "đoán" từ đầu: bộ lập kế hoạch (rules.py) đã tính sẵn nhiệt độ cảm nhận và
công suất ước tính của từng phương án. Gemini đọc bảng đó cùng bối cảnh (xu hướng, số người,
trạng thái hiện tại), chọn phương án cuối cùng và viết lý do cho người dùng.
"""
from datetime import datetime
from typing import Optional

SYSTEM_PROMPT_TEMPLATE = """Bạn là bộ điều khiển khí hậu của một phòng học/phòng làm việc thông minh (hệ thống IoT).
Mục tiêu: người trong phòng thấy thoải mái (nhiệt độ cảm nhận ≤ {comfort:.1f}°C) với ĐIỆN NĂNG THẤP NHẤT.

THIẾT BỊ (do người dùng khai báo)
{devices}
- Bạn chỉ chọn SỐ quạt bật (fan_count 0–{max_fans}); hệ thống tự chọn quạt cụ thể tốn ít điện và ít bật/tắt nhất.

CHIẾN LƯỢC (đã được bộ tính toán áp dụng để lập bảng phương án)
1. CHỈ QUẠT khi gió quạt đủ bù phần nóng: quạt tạo hiệu ứng mát ~1.5°C (1 quạt) đến ~3°C (3 quạt),
   tốn rất ít điện so với điều hòa. Dùng ít quạt nhất mà vẫn đủ mát.
2. KẾT HỢP khi quạt không đủ: bật điều hòa KÈM quạt để đặt nhiệt độ cao hơn (27–28°C),
   vì mỗi quạt cho phép nâng nhiệt độ đặt ~1–1.5°C, tiết kiệm hơn điện quạt tiêu thụ.
3. CHỈ ĐIỀU HÒA khi độ ẩm rất cao (≥ {humid:.0f}%): mồ hôi khó bay hơi nên gió quạt kém hiệu quả,
   điều hòa vừa làm mát vừa hút ẩm là đủ. Khi độ ẩm giảm, chuyển sang kết hợp.
4. Phòng trống → hệ thống tự xử lý (không hỏi bạn).
5. Tránh bật/tắt liên tục: nếu trạng thái hiện tại vẫn đạt tiện nghi và chênh lệch điện nhỏ, giữ nguyên.
   Máy nén điều hòa không nên bật/tắt quá thường xuyên.
6. Khi phòng còn nóng hơn nhiệt độ đặt nhiều (vừa bật điều hòa), có thể bật hết quạt cho mát nhanh.

CÁCH CHỌN
- Thường chọn "phương án đề xuất" của bộ tính toán. Chỉ chọn khác khi có lý do rõ ràng
  (ví dụ: tránh bật/tắt liên tục, đông người, xu hướng nhiệt độ) và nêu lý do đó.
- Không chọn phương án có nhiệt độ cảm nhận vượt ngưỡng tiện nghi nếu còn phương án đạt.

ĐẦU RA — chỉ MỘT object JSON:
{{"fan_count": <0-{max_fans}>, "ac_on": <true|false>, "ac_temp": <{ac_min}-{ac_max}>, "ai_reason": "<lý do>"}}
ai_reason: tiếng Việt có dấu, tối đa 200 ký tự, nêu số người, nhiệt độ/độ ẩm, chế độ đã chọn
(chỉ quạt / kết hợp / chỉ điều hòa / tắt) và vì sao tiết kiệm điện.
"""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "fan_count": {"type": "INTEGER", "description": "Số quạt bật, 0-3"},
        "ac_on": {"type": "BOOLEAN", "description": "Bật điều hòa hay không"},
        "ac_temp": {"type": "INTEGER", "description": "Nhiệt độ đặt điều hòa (°C)"},
        "ai_reason": {"type": "STRING", "description": "Lý do ngắn gọn bằng tiếng Việt"},
    },
    "required": ["fan_count", "ac_on", "ac_temp", "ai_reason"],
    "propertyOrdering": ["fan_count", "ac_on", "ac_temp", "ai_reason"],
}


def describe_devices(cfg) -> str:
    """Mô tả thiết bị theo hồ sơ (DeviceProfile → PlannerConfig) để đưa vào prompt."""
    lines = []
    for i, (power, ok) in enumerate(zip(cfg.fan_powers, cfg.fans_available), start=1):
        lines.append(f"- Quạt {i}: ~{power:.0f} W, chỉ bật/tắt." if ok else f"- Quạt {i}: KHÔNG dùng được.")
    if cfg.ac_available:
        lines.append(f"- 1 điều hòa: ac_on true/false, ac_temp là nhiệt độ đặt, số nguyên "
                     f"{cfg.ac_min_temp}–{cfg.ac_set_max}°C. Tốn ~{cfg.ac_power_w:.0f} W khi đặt 24°C; "
                     f"mỗi 1°C đặt cao hơn giảm khoảng {cfg.ac_saving_per_degree * 100:.0f}% điện.")
    else:
        lines.append("- Phòng KHÔNG có điều hòa dùng được: luôn trả ac_on=false.")
    return "\n".join(lines)


def build_system_prompt(cfg) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        devices=describe_devices(cfg),
        max_fans=cfg.max_fans,
        comfort=cfg.comfort_max,
        ac_min=cfg.ac_min_temp,
        ac_max=cfg.ac_set_max,
        humid=cfg.humid_rh,
    )


def _age_text(seconds: Optional[float]) -> str:
    if seconds is None:
        return "không rõ thời điểm"
    if seconds < 90:
        return f"{seconds:.0f} giây trước"
    return f"{seconds / 60:.0f} phút trước"


def build_user_prompt(
    *,
    people_count: int,
    people_age: Optional[float],
    temperature: float,
    humidity: Optional[float],
    feels_like: float,
    current: dict,
    options: list,
    recommended,
    notes: list[str],
    last_reason: Optional[str],
    now: Optional[datetime] = None,
) -> str:
    now = now or datetime.now()
    humidity_line = f"{humidity:.0f}%" if humidity is not None else "không có dữ liệu"
    ac_line = f"BẬT, đặt {current['ac_temp']}°C" if current["ac_on"] else "TẮT"

    lines = [
        "DỮ LIỆU HIỆN TẠI",
        f"- Thời gian: {now:%H:%M} ngày {now:%d/%m/%Y}",
        f"- Số người: {people_count} (camera cập nhật {_age_text(people_age)})",
        f"- Nhiệt độ phòng: {temperature:.1f}°C, độ ẩm: {humidity_line}",
        f"- Nhiệt độ cảm nhận khi không quạt/không điều hòa: ~{feels_like:.1f}°C",
        f"- Đang chạy: {current['fan_count']} quạt, điều hòa {ac_line}",
    ]
    if last_reason:
        lines.append(f"- Lý do lần trước: {last_reason}")

    lines += ["", "BẢNG PHƯƠNG ÁN (bộ tính toán ước lượng ở trạng thái ổn định)",
              "| số quạt (quạt nào) | điều hòa | cảm nhận | công suất | đạt tiện nghi |",
              "|---|---|---|---|---|"]
    for o in sorted(options, key=lambda o: o.power_w):
        ac = f"{o.ac_temp}°C" if o.ac_on else "tắt"
        fans = ", ".join(str(i + 1) for i, f in enumerate(o.fan_set) if f) or "—"
        lines.append(f"| {o.fans} ({fans}) | {ac} | {o.feels_like:.1f}°C | {o.power_w:.0f} W | "
                     f"{'có' if o.comfortable else 'không'} |")

    ac_rec = f"điều hòa {recommended.ac_temp}°C" if recommended.ac_on else "tắt điều hòa"
    lines += ["", f"PHƯƠNG ÁN ĐỀ XUẤT: {recommended.fans} quạt + {ac_rec} "
                  f"({recommended.label}, ~{recommended.power_w:.0f} W)"]
    for note in notes:
        lines.append(f"- Lưu ý: {note}")
    lines += ["", "Hãy chọn cấu hình cuối cùng (JSON)."]
    return "\n".join(lines)
