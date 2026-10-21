"""Pydantic schemas for comment requests and responses."""

from pydantic import BaseModel
from typing import Optional


class CommentCreate(BaseModel):
    post_id: int
    author_id: int
    body: str


class CommentResponse(BaseModel):
    id: int
    post_id: int
    author_id: int
    body: str
    created_at: Optional[str] = None
