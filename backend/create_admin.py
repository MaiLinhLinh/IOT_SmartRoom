"""
create_admin.py — Tạo tài khoản admin (không reset DB).

Chạy tương tác:
    python create_admin.py

Chạy tự động:
    python create_admin.py --username admin --password admin123 --role admin
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
import models  # noqa: F401
from models import User

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def main() -> None:
    parser = argparse.ArgumentParser(description="Tạo tài khoản admin cho AIoT")
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--full-name", default="Administrator")
    parser.add_argument("--role", default="admin", choices=["admin", "user"])
    args = parser.parse_args()

    username = args.username or input("Nhap username: ").strip()
    password = args.password or input("Nhap password: ").strip()

    if not username or not password:
        print("Username va password khong duoc de trong!")
        sys.exit(1)
    if len(password) < 6:
        print("Password phai >= 6 ky tu!")
        sys.exit(1)

    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == username).first()
        if existing:
            existing.password = pwd_ctx.hash(password)
            existing.role = args.role
            db.commit()
            print(f"Da cap nhat tai khoan '{username}' (role={args.role})")
            return

        db.add(User(
            username=username,
            password=pwd_ctx.hash(password),
            full_name=args.full_name,
            role=args.role,
        ))
        db.commit()
        print(f"Tao tai khoan '{username}' thanh cong!")
        print("Dang nhap qua POST /auth/login")
    finally:
        db.close()


if __name__ == "__main__":
    main()
