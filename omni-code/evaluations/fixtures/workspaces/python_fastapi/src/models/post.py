"""Blog post domain model."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Post:
    id: int
    author_id: int
    title: str
    body: str
    published: bool = False
    created_at: Optional[str] = None

    def summary(self, max_length: int = 100) -> str:
        if len(self.body) <= max_length:
            return self.body
        return self.body[:max_length].rsplit(" ", 1)[0] + "..."
