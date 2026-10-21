"""Pydantic schemas for blog post requests and responses."""

from pydantic import BaseModel
from typing import Optional


class PostCreate(BaseModel):
    author_id: int
    title: str
    body: str


class PostUpdate(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    published: Optional[bool] = None


class PostResponse(BaseModel):
    id: int
    author_id: int
    title: str
    body: str
    published: bool
    created_at: Optional[str] = None
