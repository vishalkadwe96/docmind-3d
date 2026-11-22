"""Password hashing (scrypt, stdlib) + JWT auth dependency."""
import hashlib
import hmac
import os
import secrets
import time
from typing import Optional

import jwt  # pip install pyjwt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import db

load_dotenv()

SECRET = os.getenv("JWT_SECRET")
if not SECRET:
    SECRET = secrets.token_hex(32)
    print("WARNING: JWT_SECRET not set. Using a temporary secret; everyone is logged out on restart.")

TOKEN_TTL = 12 * 3600  # seconds
_SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, **_SCRYPT)
    return f"{salt.hex()}${h.hex()}"


_DUMMY = hash_password("dummy-password")  # used so unknown usernames take the same time


def verify_password(pw: str, stored) -> bool:
    try:
        salt_hex, h_hex = (stored or _DUMMY).split("$")
        h = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
        ok = hmac.compare_digest(h, bytes.fromhex(h_hex))
    except Exception:
        return False
    return ok and stored is not None


def make_token(user_id: int) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": str(user_id), "iat": now, "exp": now + TOKEN_TTL}, SECRET, algorithm="HS256"
    )


_bearer = HTTPBearer(auto_error=False)


def current_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)):
    """FastAPI dependency: returns the logged-in user dict or raises 401."""
    if creds is None:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, SECRET, algorithms=["HS256"])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(401, "Invalid or expired token")
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(401, "User no longer exists")
    return user