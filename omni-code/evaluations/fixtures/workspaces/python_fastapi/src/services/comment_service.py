"""Comment business logic."""

from datetime import datetime, timezone
from typing import Optional

from src import database


def create_comment(post_id: int, author_id: int, body: str) -> dict:
    post = database.get("posts", post_id)
    if not post:
        raise ValueError(f"Post not found: {post_id}")
    if not body.strip():
        raise ValueError("Comment body cannot be empty")
    return database.insert("comments", {
        "post_id": post_id,
        "author_id": author_id,
        "body": body,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


def get_comments_for_post(post_id: int) -> list:
    return database.query("comments", post_id=post_id)


def delete_comment(comment_id: int) -> bool:
    return database.delete("comments", comment_id)
