"""
routers/sensor.py — Nhận dữ liệu cảm biến từ ESP32, trả dữ liệu mới nhất cho Flutter
POST /api/sensor          ← ESP32 gửi mỗi 5 giây (không cần auth)
GET  /api/sensor/latest   ← Flutter lấy để hiển thị Dashboard
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime

from database import get_db
from models import SensorLog
from schemas import SensorDataIn, SensorDataOut
from services.device_service import get_state

router = APIRouter(prefix="/api/sensor", tags=["Sensor"])


@router.post("", status_code=201, summary="ESP32 gửi dữ liệu cảm biến")
def receive_sensor(body: SensorDataIn, db: Session = Depends(get_db)):
    """
    ESP32 gọi endpoint này mỗi 5 giây với {temperature, humidity}.
    Backend lưu snapshot vào sensor_logs kèm trạng thái thiết bị + số người mới nhất.
    Không yêu cầu xác thực — ESP32 không có JWT.
    """
    # Lấy trạng thái hiện tại (tự tạo row nếu thiếu)
    state = get_state(db)

    log = SensorLog(
        temperature  = body.temperature,
        humidity     = body.humidity,
        people_count = state.people_count or 0,     # YOLO service cập nhật qua /api/people/count
        fan1         = state.fan1,
        fan2         = state.fan2,
        fan3         = state.fan3,
        ac_on        = state.ac_on,
        ac_temp      = state.ac_temp,
        mode         = state.mode,
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    return {
        "status":  "ok",
        "log_id":  log.id,
        "message": f"Đã lưu T={body.temperature}°C H={body.humidity}%"
    }


@router.get("/latest", response_model=SensorDataOut, summary="Dữ liệu cảm biến mới nhất")
def get_latest(db: Session = Depends(get_db)):
    """
    Flutter gọi để hiển thị nhiệt độ/độ ẩm realtime trên Dashboard.
    Trả về row mới nhất trong sensor_logs.
    """
    log = (
        db.query(SensorLog)
        .order_by(SensorLog.id.desc())
        .first()
    )
    if not log:
        raise HTTPException(status_code=404, detail="Chưa có dữ liệu cảm biến nào")
    return log
