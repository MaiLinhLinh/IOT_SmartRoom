"""
routers/auth.py v3 — Auth + Profile (Trang 1 Login, Trang 5 Profile)
POST /auth/register
POST /auth/login       → JSON (Flutter dùng)
POST /auth/token       → form OAuth2 (Swagger Authorize dùng)
POST /auth/logout
POST /auth/stream-token  → token ngắn hạn xem camera
GET  /auth/profile
PUT  /auth/profile
PUT  /auth/change-password
"""
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from jose import jwt, JWTError
from passlib.context import CryptContext
from dotenv import load_dotenv

from database import get_db
from models import User
from schemas import (
    LoginRequest, RegisterRequest, TokenResponse, UserOut,
    UserProfileUpdate, ChangePasswordRequest, StreamTokenOut
)

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "aiot_dev_secret")
ALGORITHM  = os.getenv("ALGORITHM", "HS256")
EXPIRE_MIN = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))
try:
    STREAM_TOKEN_EXPIRE_SECONDS = max(30, int(os.getenv("STREAM_TOKEN_EXPIRE_SECONDS", "120")))
except ValueError:
    STREAM_TOKEN_EXPIRE_SECONDS = 120

router  = APIRouter(prefix="/auth", tags=["Auth"])
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2  = OAuth2PasswordBearer(tokenUrl="/auth/token")


# ===== HELPER =====

def create_token(data: dict) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + timedelta(minutes=EXPIRE_MIN)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_stream_token(user: User) -> str:
    """JWT sống ngắn, chỉ dùng để mở stream camera qua query string."""
    payload = {
        "sub":   user.username,
        "uid":   user.id,
        "scope": "camera_stream",
        "exp":   datetime.now(timezone.utc) + timedelta(seconds=STREAM_TOKEN_EXPIRE_SECONDS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2), db: Session = Depends(get_db)) -> User:
    """Dependency: xác thực JWT, trả User object."""
    try:
        payload  = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Token không hợp lệ")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token hết hạn hoặc không hợp lệ")

    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="Người dùng không tồn tại")
    return user


def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Chỉ admin được phép thực hiện")
    return current_user


def _authenticate(db: Session, username: str, password: str) -> User:
    user = db.query(User).filter(User.username == username).first()
    if not user or not pwd_ctx.verify(password, user.password):
        raise HTTPException(status_code=401, detail="Sai username hoặc password")
    return user


def _token_response(user: User) -> TokenResponse:
    token = create_token({"sub": user.username, "uid": user.id, "role": user.role})
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


# ===== ENDPOINTS =====

@router.post("/register", status_code=201, summary="Tạo tài khoản")
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(status_code=400, detail="Username đã tồn tại")
    user = User(
        username  = body.username,
        password  = pwd_ctx.hash(body.password),
        full_name = body.full_name,
        email     = body.email,
        role      = "user",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"message": f"Tạo tài khoản '{body.username}' thành công", "id": user.id}


@router.post("/login", response_model=TokenResponse, summary="Đăng nhập (Flutter)")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = _authenticate(db, body.username, body.password)
    return _token_response(user)


@router.post("/token", response_model=TokenResponse, summary="Đăng nhập (OAuth2 form)")
def login_form(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Dùng cho nút Authorize trên Swagger UI."""
    user = _authenticate(db, form.username, form.password)
    return _token_response(user)


@router.post("/logout", summary="Đăng xuất (client tự xoá token)")
def logout():
    return {"message": "Đã đăng xuất"}


@router.post("/stream-token", response_model=StreamTokenOut, summary="Token ngắn hạn xem camera từ xa")
def get_stream_token(current_user: User = Depends(get_current_user)):
    """
    Flutter gọi API này rồi ghép token vào URL:
    GET /api/camera/stream?token=<stream_token>
    """
    return StreamTokenOut(
        stream_token = create_stream_token(current_user),
        expires_in   = STREAM_TOKEN_EXPIRE_SECONDS,
        stream_path  = "/api/camera/stream",
    )


@router.get("/profile", response_model=UserOut, summary="Xem profile (Trang 5)")
def get_profile(current_user: User = Depends(get_current_user)):
    return current_user


@router.put("/profile", response_model=UserOut, summary="Cập nhật profile (Trang 5)")
def update_profile(
    body:         UserProfileUpdate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    if body.full_name is not None:
        current_user.full_name = body.full_name
    if body.email is not None:
        current_user.email = body.email
    db.commit()
    db.refresh(current_user)
    return current_user


@router.put("/change-password", summary="Đổi mật khẩu (Trang 5)")
def change_password(
    body:         ChangePasswordRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    if not pwd_ctx.verify(body.old_password, current_user.password):
        raise HTTPException(status_code=400, detail="Mật khẩu cũ không đúng")
    current_user.password = pwd_ctx.hash(body.new_password)
    db.commit()
    return {"message": "Đổi mật khẩu thành công"}
