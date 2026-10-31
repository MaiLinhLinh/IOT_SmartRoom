"""
routers/people.py — Cầu nối cho module YOLO chạy riêng.

YOLO service (bạn của bạn) chỉ cần gọi:
POST /api/people/count
Header: X-API-Key: <PEOPLE_API_KEY>   (bỏ qua nếu .env để trống)
Body:   {"count": 3, "source": "YOLO"}

Backend lưu vào device_state.people_count; AI engine và Flutter đọc lại.
"""
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from schemas import PeopleCountIn, PeopleLatestOut
from services.device_service import get_state

router = APIRouter(prefix="/api/people", tags=["People"])

PEOPLE_API_KEY = os.getenv("PEOPLE_API_KEY", "")


def verify_people_key(x_api_key: Optional[str] = Header(default=None)):
    """Nếu .env có PEOPLE_API_KEY thì bắt buộc header X-API-Key khớp."""
    if PEOPLE_API_KEY and x_api_key != PEOPLE_API_KEY:
        raise HTTPException(status_code=401, detail="X-API-Key không hợp lệ")


@router.post("/count", summary="YOLO service cập nhật số người", dependencies=[Depends(verify_people_key)])
def update_people_count(body: PeopleCountIn, db: Session = Depends(get_db)):
    state = get_state(db)
    state.people_count = body.count
    state.people_source = body.source
    state.people_updated_at = datetime.now()
    db.commit()
    db.refresh(state)
    return {
        "status": "ok",
        "people_count": state.people_count,
        "people_source": state.people_source,
        "people_updated_at": state.people_updated_at,
    }


@router.get("/latest", response_model=PeopleLatestOut, summary="Số người mới nhất")
def get_people_latest(db: Session = Depends(get_db)):
    """Flutter dùng cho trang Camera / Dashboard."""
    state = get_state(db)
    return PeopleLatestOut(
        people_count=state.people_count or 0,
        people_source=state.people_source or "NONE",
        people_updated_at=state.people_updated_at,
    )
