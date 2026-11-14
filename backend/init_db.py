"""
init_db.py — Khởi tạo / reset database theo schema v4 (3 quạt độc lập).

Ví dụ:
    python init_db.py --reset --admin-username admin --admin-password admin123
    python init_db.py                      # chỉ tạo bảng còn thiếu + seed state
"""
import argparse
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from passlib.context import CryptContext

from database import Base, SessionLocal, engine
import models  # noqa: F401 — bắt buộc import để metadata biết các bảng
from models import DeviceState, User

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def main() -> None:
    parser = argparse.ArgumentParser(description="Khởi tạo database AIoT")
    parser.add_argument("--reset", action="store_true", help="DROP toàn bộ bảng rồi tạo lại (mất dữ liệu)")
    parser.add_argument("--admin-username", default=None, help="Tạo tài khoản admin")
    parser.add_argument("--admin-password", default=None, help="Mật khẩu admin")
    parser.add_argument("--admin-full-name", default="Administrator")
    args = parser.parse_args()

    if args.reset:
        Base.metadata.drop_all(bind=engine)
        print("[init_db] Đã drop toàn bộ bảng cũ")

    Base.metadata.create_all(bind=engine)
    print("[init_db] Đã tạo/cập nhật bảng theo schema v4")

    db = SessionLocal()
    try:
        if not db.get(DeviceState, 1):
            db.add(DeviceState(id=1))
            print("[init_db] Đã tạo device_state(id=1)")

        if args.admin_username:
            password = args.admin_password
            if not password:
                import getpass
                password = getpass.getpass("Nhập mật khẩu admin: ")
            if len(password) < 6:
                print("[init_db] Mật khẩu phải >= 6 ký tự", file=sys.stderr)
                sys.exit(1)

            existing = db.query(User).filter(User.username == args.admin_username).first()
            if existing:
                existing.role = "admin"
                existing.password = pwd_ctx.hash(password)
                print(f"[init_db] Đã cập nhật admin '{args.admin_username}' (mật khẩu mới)")
            else:
                db.add(User(
                    username=args.admin_username,
                    password=pwd_ctx.hash(password),
                    full_name=args.admin_full_name,
                    role="admin",
                ))
                print(f"[init_db] Đã tạo admin '{args.admin_username}'")

        db.commit()
    finally:
        db.close()

    print("[init_db] Hoàn tất")


if __name__ == "__main__":
    main()
