"""Blog post API routes."""

from fastapi import APIRouter

from src.schemas.post_schema import PostCreate, PostUpdate
from src.services import post_service

router = APIRouter(prefix="/posts", tags=["posts"])


@router.get("/")
async def list_posts(published_only: bool = False):
    return post_service.list_posts(published_only=published_only)


@router.get("/{post_id}")
async def get_post(post_id: int):
    post = post_service.get_post(post_id)
    if not post:
        return {"error": "Post not found"}, 404
    return post


@router.post("/", status_code=201)
async def create_post(payload: PostCreate):
    try:
        return post_service.create_post(payload.author_id, payload.title, payload.body)
    except ValueError as exc:
        return {"error": str(exc)}, 422


@router.patch("/{post_id}")
async def update_post(post_id: int, payload: PostUpdate):
    result = post_service.update_post(post_id, **payload.model_dump())
    if not result:
        return {"error": "Post not found"}, 404
    return result


@router.post("/{post_id}/publish")
async def publish_post(post_id: int):
    result = post_service.publish_post(post_id)
    if not result:
        return {"error": "Post not found"}, 404
    return result
