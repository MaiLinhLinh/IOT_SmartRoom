"""
models.py — SQLAlchemy ORM models (schema v3)
Bao gồm: sensor_logs, device_state, users, control_logs
"""
from sqlalchemy import (
    Column, Integer, Float, Unicode, Boolean, DateTime, ForeignKey
)
from sqlalchemy.sql import func
from database import Base


class SensorLog(Base):
    """sensor_logs: ESP32 gửi mỗi 5 giây."""
    __tablename__ = "sensor_logs"

    id           = Column(Integer, primary_key=True, index=True)
    timestamp    = Column(DateTime, server_default=func.getdate(), nullable=False, index=True)
    temperature  = Column(Float, nullable=True)
    humidity     = Column(Float, nullable=True)
    people_count = Column(Integer, nullable=False, default=0)
    fan_level    = Column(Integer, nullable=False, default=0)
    ac_on        = Column(Boolean, nullable=False, default=False)
    ac_temp      = Column(Integer, nullable=False, default=26)
    mode         = Column(Unicode(10), nullable=False, default="MANUAL")


class DeviceState(Base):
    """device_state: luôn 1 row (id=1), trạng thái hiện tại của phòng."""
    __tablename__ = "device_state"

    id                = Column(Integer, primary_key=True, default=1)
    fan_level         = Column(Integer, nullable=False, default=0)
    ac_on             = Column(Boolean, nullable=False, default=False)
    ac_temp           = Column(Integer, nullable=False, default=26)
    mode              = Column(Unicode(10), nullable=False, default="MANUAL")
    updated_at        = Column(DateTime, server_default=func.getdate(), onupdate=func.getdate())
    ai_reason         = Column(Unicode(500), nullable=True)
    people_count      = Column(Integer, nullable=False, default=0)
    people_source     = Column(Unicode(20), nullable=False, default="NONE")  # YOLO | MANUAL | AI | NONE
    people_updated_at = Column(DateTime, nullable=True)


class User(Base):
    """users: tài khoản đăng nhập."""
    __tablename__ = "users"

    id         = Column(Integer, primary_key=True, index=True)
    username   = Column(Unicode(50), unique=True, nullable=False, index=True)
    password   = Column(Unicode(255), nullable=False)
    full_name  = Column(Unicode(100), nullable=True)
    email      = Column(Unicode(100), nullable=True)
    created_at = Column(DateTime, server_default=func.getdate(), nullable=False)
    role       = Column(Unicode(20), nullable=False, default="user")  # "admin" | "user"


class ControlLog(Base):
    """
    control_logs: lịch sử từng thao tác điều khiển (Trang 6).
    Ghi lại: ai làm, làm gì, lúc nào, trước/sau, bối cảnh cảm biến.
    """
    __tablename__ = "control_logs"

    id             = Column(Integer, primary_key=True, index=True)
    timestamp      = Column(DateTime, server_default=func.getdate(), nullable=False, index=True)
    actor          = Column(Unicode(50), nullable=False)                 # username hoặc "AI"
    actor_type     = Column(Unicode(10), nullable=False, index=True)     # "USER" | "AI"
    actor_user_id  = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    source         = Column(Unicode(20), nullable=False, default="MANUAL")  # "MANUAL" | "AI"
    description    = Column(Unicode(500), nullable=False)

    fan_level      = Column(Integer, nullable=False)
    ac_on          = Column(Boolean, nullable=False)
    ac_temp        = Column(Integer, nullable=False)
    mode           = Column(Unicode(10), nullable=False)

    fan_level_prev = Column(Integer, nullable=True)
    ac_on_prev     = Column(Boolean, nullable=True)
    ac_temp_prev   = Column(Integer, nullable=True)

    ai_reason      = Column(Unicode(500), nullable=True)
    temperature    = Column(Float, nullable=True)   # bối cảnh lúc thao tác
    humidity       = Column(Float, nullable=True)
    people_count   = Column(Integer, nullable=True)
