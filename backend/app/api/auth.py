"""Authentication endpoints for public users (POST /auth/register,
POST /auth/login) -- JWT-based, mirroring the AdminUser login flow in
app/api/admin_auth.py but minting user-scoped tokens (see
app/core/security.py::create_user_token / get_current_user).

User accounts exist so logged-in users can submit and vote on
crowd-sourced route/stop suggestions (app/api/suggestions.py); the
suggestion/vote endpoints are the only ones that require this token.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.rate_limit import limiter
from app.core.security import create_user_token, hash_password, verify_password
from app.db.session import get_db
from app.models import User

from app.schemas import UserLoginRequest, UserRegisterRequest, UserTokenResponse

router = APIRouter(tags=["auth"])


def _user_token(user: User) -> UserTokenResponse:
    return UserTokenResponse(access_token=create_user_token(user_id=user.user_id, username=user.username))


@router.post("/auth/register", response_model=UserTokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def register(request: Request, payload: UserRegisterRequest, db: Session = Depends(get_db)) -> UserTokenResponse:
    existing = db.scalar(select(User).where(User.username == payload.username))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username is already taken.")

    user = User(username=payload.username, password_hash=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    # Auto-login: registration mints a token directly so the frontend doesn't
    # need a separate round-trip before letting the user start voting.
    return _user_token(user)


@router.post("/auth/login", response_model=UserTokenResponse)
@limiter.limit("5/minute")
def login(request: Request, payload: UserLoginRequest, db: Session = Depends(get_db)) -> UserTokenResponse:
    user = db.scalar(select(User).where(User.username == payload.username))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password.")

    return _user_token(user)