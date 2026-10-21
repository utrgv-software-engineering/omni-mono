"""User domain model."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class User:
    id: int
    name: str
    email: str
    is_active: bool = True
    bio: Optional[str] = None

    def display_name(self):
        return self.name.title()
