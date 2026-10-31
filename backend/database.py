"""
database.py — Kết nối SQL Server qua SQLAlchemy + pyodbc
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

load_dotenv()

DB_URL = os.getenv("DB_URL")

if not DB_URL:
    raise ValueError("DB_URL chưa được cấu hình trong file .env")

# fast_executemany giúp insert nhiều row nhanh hơn (pyodbc)
engine = create_engine(
    DB_URL,
    echo=False,          # Đặt True nếu muốn xem SQL log
    pool_pre_ping=True,  # Tự ping để phát hiện kết nối chết
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# Dependency dùng trong FastAPI routers
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
