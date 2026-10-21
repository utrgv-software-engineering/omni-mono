"""Pydantic schemas for user requests and responses."""

from pydantic import BaseModel, EmailStr
from typing import Optional


class UserCreate(BaseModel):
    name: str
    email: str
    bio: Optional[str] = None


class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    is_active: bool
    bio: Optional[str] = None
