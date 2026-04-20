# Autor: GreenHornet
# Date: 2026/4/20
# Description: User schemas for request/response validation.

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, EmailStr

class UserCreate(BaseModel):
    """User registration request."""
    account: str = Field(..., min_length=3, max_length=255)
    name: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=6, max_length=255)
    email: Optional[str] = None
    oid: int = Field(default=1)
    language: str = Field(default="zh-CN")


class UserLogin(BaseModel):
    """User login request."""
    account: str
    password: str


class UserResponse(BaseModel):
    """User response (without password)."""
    id: int
    account: str
    name: str
    email: Optional[str] = None
    oid: int
    status: int
    language: str
    origin: int
    create_time: int

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    """JWT token response."""
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    """JWT token payload."""
    sub: int  # user id
    exp: Optional[int] = None