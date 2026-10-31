"""
routers/control_logs.py — Lịch sử thao tác điều khiển (Trang 6)
GET /api/control-logs            ← Danh sách phân trang + lọc
GET /api/control-logs/summary    ← Thống kê nhanh
GET /api/control-logs/{id}       ← Chi tiết 1 thao tác
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from database import get_db
from models import ControlLog
from schemas import ControlLogOut, ControlLogListOut

router = APIRouter(prefix="/api/control-logs", tags=["Control Logs"])


@router.get("", response_model=ControlLogListOut, summary="Lịch sử thao tác (Trang 6)")
def get_control_logs(
    page:       int = Query(default=1,  ge=1),
    size:       int = Query(default=20, ge=1, le=100),
    actor_type: Optional[str]      = Query(default=None, pattern="^(USER|AI)$", description="Lọc USER hoặc AI"),
    actor:      Optional[str]      = Query(default=None, description="Lọc theo username / 'AI'"),
    source:     Optional[str]      = Query(default=None, pattern="^(MANUAL|AI)$", description="Nguồn lệnh"),
    search:     Optional[str]      = Query(default=None, description="Tìm trong mô tả"),
    from_date:  Optional[datetime] = Query(default=None, description="Từ thời điểm (ISO 8601)"),
    to_date:    Optional[datetime] = Query(default=None, description="Đến thời điểm (ISO 8601)"),
    db: Session = Depends(get_db),
):
    """
    Trang 6: ai làm, làm gì, lúc nào, trước/sau, bối cảnh cảm biến.
    Ví dụ: /api/control-logs?actor_type=AI&search=điều hòa
    """
    query = db.query(ControlLog)
    if actor_type:
        query = query.filter(ControlLog.actor_type == actor_type.upper())
    if actor:
        query = query.filter(ControlLog.actor == actor)
    if source:
        query = query.filter(ControlLog.source == source.upper())
    if search:
        query = query.filter(or_(
            ControlLog.description.ilike(f"%{search}%"),
            ControlLog.actor.ilike(f"%{search}%"),
        ))
    if from_date:
        query = query.filter(ControlLog.timestamp >= from_date)
    if to_date:
        query = query.filter(ControlLog.timestamp <= to_date)

    total  = query.count()
    offset = (page - 1) * size
    items  = query.order_by(ControlLog.id.desc()).offset(offset).limit(size).all()
    return ControlLogListOut(total=total, page=page, size=size, items=items)


@router.get("/summary", summary="Tóm tắt lịch sử thao tác")
def get_summary(db: Session = Depends(get_db)):
    """Thống kê cho Dashboard/trang 6."""
    total      = db.query(ControlLog).count()
    user_count = db.query(ControlLog).filter(ControlLog.actor_type == "USER").count()
    ai_count   = db.query(ControlLog).filter(ControlLog.actor_type == "AI").count()
    latest     = db.query(ControlLog).order_by(ControlLog.id.desc()).first()

    since = datetime.now() - timedelta(hours=24)
    last24h = db.query(ControlLog).filter(ControlLog.timestamp >= since).count()

    top_actors = (
        db.query(ControlLog.actor, func.count(ControlLog.id).label("count"))
        .group_by(ControlLog.actor)
        .order_by(func.count(ControlLog.id).desc())
        .limit(5)
        .all()
    )

    return {
        "total_actions": total,
        "user_actions":  user_count,
        "ai_actions":    ai_count,
        "actions_last_24h": last24h,
        "top_actors": [{"actor": a, "count": c} for a, c in top_actors],
        "latest": {
            "actor":       latest.actor if latest else None,
            "description": latest.description if latest else None,
            "timestamp":   latest.timestamp if latest else None,
        },
    }


@router.get("/{log_id}", response_model=ControlLogOut, summary="Chi tiết 1 thao tác")
def get_control_log_detail(log_id: int, db: Session = Depends(get_db)):
    log = db.query(ControlLog).filter(ControlLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy control_log id={log_id}")
    return log
