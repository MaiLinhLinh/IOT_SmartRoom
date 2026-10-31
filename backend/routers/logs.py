"""
routers/logs.py — Xem lịch sử log cảm biến (Trang 4)
GET /api/logs          ← Danh sách log phân trang + lọc thời gian/mode
GET /api/logs/{id}     ← Chi tiết 1 log
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models import SensorLog
from schemas import LogOut, LogListOut

router = APIRouter(prefix="/api/logs", tags=["Logs"])


@router.get("", response_model=LogListOut, summary="Danh sách log phân trang")
def get_logs(
    page:      int = Query(default=1,  ge=1, description="Số trang"),
    size:      int = Query(default=20, ge=1, le=100, description="Số item mỗi trang"),
    mode:      Optional[str]      = Query(default=None, pattern="^(MANUAL|AUTO)$", description="Lọc theo chế độ"),
    from_date: Optional[datetime] = Query(default=None, description="Từ thời điểm (ISO 8601)"),
    to_date:   Optional[datetime] = Query(default=None, description="Đến thời điểm (ISO 8601)"),
    db: Session = Depends(get_db),
):
    """
    Flutter dùng để hiển thị màn hình Sensor Logs (trang 4).
    Ví dụ: /api/logs?page=1&size=20&mode=AUTO&from_date=2026-10-01T00:00:00
    """
    query = db.query(SensorLog)
    if mode:
        query = query.filter(SensorLog.mode == mode)
    if from_date:
        query = query.filter(SensorLog.timestamp >= from_date)
    if to_date:
        query = query.filter(SensorLog.timestamp <= to_date)

    total  = query.count()
    offset = (page - 1) * size
    items  = query.order_by(SensorLog.id.desc()).offset(offset).limit(size).all()
    return LogListOut(total=total, page=page, size=size, items=items)


@router.get("/{log_id}", response_model=LogOut, summary="Chi tiết 1 log")
def get_log_detail(log_id: int, db: Session = Depends(get_db)):
    log = db.query(SensorLog).filter(SensorLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy log id={log_id}")
    return log
