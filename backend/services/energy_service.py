"""
services/energy_service.py — Tính toán điện năng tiêu thụ theo ngày.

Nguyên lý:
- ESP32 gửi sensor mỗi 5 giây (POST /api/sensor). Mỗi log lưu trạng thái
  fan1/fan2/fan3/ac_on tại thời điểm đó.
- Mỗi lần có log mới, backend lấy log TRƯỚC đó, nhân khoảng thời gian giữa
  2 log với tổng công suất các thiết bị ĐANG BẬT (theo log trước — trạng thái
  của khoảng thời gian vừa qua), rồi cộng dồn vào energy_daily của ngày hiện tại.
- Công suất lấy từ device_info; nếu người dùng chưa nhập (NULL) hoặc row thiếu,
  dùng giá trị mặc định bên dưới.

Giới hạn đã chốt với chủ dự án:
- Coi AC "bật = chạy hết công suất danh định" (không mô phỏng máy nén).
- Khoảng cách giữa 2 log > MAX_INTERVAL_HOURS (backend dừng lâu) thì BỎ qua
  khoảng đó để tránh cộng sai số lớn từ dữ liệu lỗi thời.
"""
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from models import DeviceInfo, EnergyDaily, SensorLog

# Công suất mặc định khi người dùng không nhập (đã chốt: quạt 45W, AC 900W)
DEFAULT_POWER_WATTS = {
    "fan1": 45.0,
    "fan2": 45.0,
    "fan3": 45.0,
    "ac":   900.0,
}

DEVICE_KEYS = ("fan1", "fan2", "fan3", "ac")

DEVICE_NAMES = {
    "fan1": "Quạt 1",
    "fan2": "Quạt 2",
    "fan3": "Quạt 3",
    "ac":   "Điều hòa",
}

# Khoảng cách 2 log lớn hơn giá trị này (giờ) → bỏ qua, không tính điện
MAX_INTERVAL_HOURS = 1.0


def seed_device_info(db: Session) -> None:
    """Đảm bảo device_info có đủ 4 row (chỉ thêm row còn thiếu)."""
    for key in DEVICE_KEYS:
        if not db.get(DeviceInfo, key):
            db.add(DeviceInfo(device_key=key, device_name=DEVICE_NAMES[key]))
    db.commit()


def get_power_map(db: Session) -> dict[str, float]:
    """Công suất hiệu dụng từng thiết bị (W): giá trị người dùng nhập hoặc mặc định."""
    power = dict(DEFAULT_POWER_WATTS)
    for info in db.query(DeviceInfo).all():
        if info.power_watts is not None:
            power[info.device_key] = float(info.power_watts)
    return power


def accumulate_energy(db: Session, prev_log: Optional[SensorLog]) -> None:
    """
    Cộng điện năng của khoảng (prev_log → bây giờ) vào energy_daily hôm nay.
    Gọi trong POST /api/sensor TRƯỚC khi commit; caller chịu trách nhiệm commit.
    """
    if prev_log is None:
        return

    now = datetime.now()
    delta_hours = (now - prev_log.timestamp).total_seconds() / 3600.0
    if delta_hours <= 0 or delta_hours > MAX_INTERVAL_HOURS:
        return

    power = get_power_map(db)
    today = now.date()

    row = db.get(EnergyDaily, today)
    if not row:
        row = EnergyDaily(
            date=today, fan1_wh=0.0, fan2_wh=0.0, fan3_wh=0.0, ac_wh=0.0, total_wh=0.0
        )
        db.add(row)

    if prev_log.fan1:
        row.fan1_wh += power["fan1"] * delta_hours
    if prev_log.fan2:
        row.fan2_wh += power["fan2"] * delta_hours
    if prev_log.fan3:
        row.fan3_wh += power["fan3"] * delta_hours
    if prev_log.ac_on:
        row.ac_wh   += power["ac"] * delta_hours

    row.total_wh = row.fan1_wh + row.fan2_wh + row.fan3_wh + row.ac_wh


def get_daily(db: Session, days: int) -> list[EnergyDaily]:
    """
    Điện năng `days` ngày gần nhất, trả ĐỦ `days` phần tử liên tục theo ngày
    (ngày thiếu dữ liệu → row 0) để frontend vẽ biểu đồ cột không bị hụt cột.
    """
    today = date.today()
    start = today - timedelta(days=days - 1)
    rows = {
        r.date: r
        for r in db.query(EnergyDaily).filter(EnergyDaily.date >= start).all()
    }
    result = []
    for i in range(days):
        d = start + timedelta(days=i)
        # Ngày chưa có dữ liệu → row 0 (không add vào session, chỉ để trả API)
        result.append(rows.get(d) or EnergyDaily(
            date=d, fan1_wh=0.0, fan2_wh=0.0, fan3_wh=0.0, ac_wh=0.0, total_wh=0.0
        ))
    return result


def get_summary(db: Session) -> dict:
    """Số liệu nhanh cho widget Dashboard (hôm nay + trung bình 7 ngày)."""
    today = date.today()
    week = get_daily(db, 7)

    today_row = week[-1]  # phần tử cuối của get_daily luôn là hôm nay
    days_with_data = [r for r in week if r.total_wh > 0]
    avg = sum(r.total_wh for r in days_with_data) / len(days_with_data) if days_with_data else 0.0

    per_device = {
        "fan1": today_row.fan1_wh,
        "fan2": today_row.fan2_wh,
        "fan3": today_row.fan3_wh,
        "ac":   today_row.ac_wh,
    }
    top_device = max(per_device, key=per_device.get) if today_row.total_wh > 0 else None

    return {
        "today_wh":      round(today_row.total_wh, 1),
        "fan1_wh":       round(today_row.fan1_wh, 1),
        "fan2_wh":       round(today_row.fan2_wh, 1),
        "fan3_wh":       round(today_row.fan3_wh, 1),
        "ac_wh":         round(today_row.ac_wh, 1),
        "avg_7d_wh":     round(avg, 1),
        "top_device":    top_device,
        "days_recorded": len(days_with_data),
    }
