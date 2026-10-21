"""Blog post business logic."""

from datetime import datetime, timezone
from typing import Optional

from src import database


def create_post(author_id: int, title: str, body: str) -> dict:
    author = database.get("users", author_id)
    if not author:
        raise ValueError(f"Author not found: {author_id}")
    if not title.strip():
        raise ValueError("Post title cannot be empty")
    return database.insert("posts", {
        "author_id": author_id,
        "title": title,
        "body": body,
        "published": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


def get_post(post_id: int) -> Optional[dict]:
    return database.get("posts", post_id)


def list_posts(published_only: bool = False) -> list:
    posts = database.get_all("posts")
    if published_only:
        return [p for p in posts if p.get("published")]
    return posts


def publish_post(post_id: int) -> Optional[dict]:
    return database.update("posts", post_id, {"published": True})


def update_post(post_id: int, **fields) -> Optional[dict]:
    post = database.get("posts", post_id)
    if not post:
        return None
    allowed = {"title", "body", "published"}
    filtered = {k: v for k, v in fields.items() if k in allowed and v is not None}
    return database.update("posts", post_id, filtered)
