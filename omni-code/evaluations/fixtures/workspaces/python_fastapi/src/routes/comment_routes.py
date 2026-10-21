"""Comment API routes."""

from fastapi import APIRouter

from src.schemas.comment_schema import CommentCreate
from src.services import comment_service

router = APIRouter(prefix="/comments", tags=["comments"])


@router.get("/post/{post_id}")
async def list_comments(post_id: int):
    return comment_service.get_comments_for_post(post_id)


@router.post("/", status_code=201)
async def create_comment(payload: CommentCreate):
    try:
        return comment_service.create_comment(
            payload.post_id, payload.author_id, payload.body
        )
    except ValueError as exc:
        return {"error": str(exc)}, 422


@router.delete("/{comment_id}")
async def delete_comment(comment_id: int):
    if not comment_service.delete_comment(comment_id):
        return {"error": "Comment not found"}, 404
    return {"status": "deleted"}
