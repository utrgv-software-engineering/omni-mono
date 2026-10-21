"""Comment domain model."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Comment:
    id: int
    post_id: int
    author_id: int
    body: str
    created_at: Optional[str] = None
