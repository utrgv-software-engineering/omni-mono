"""User API routes."""

from fastapi import APIRouter

from src.schemas.user_schema import UserCreate
from src.services import user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/")
async def list_users():
    return user_service.list_users()


@router.get("/{user_id}")
async def get_user(user_id: int):
    user = user_service.get_user(user_id)
    if not user:
        return {"error": "User not found"}, 404
    return user


@router.post("/", status_code=201)
async def create_user(payload: UserCreate):
    try:
        return user_service.create_user(payload.name, payload.email, payload.bio)
    except ValueError as exc:
        return {"error": str(exc)}, 422


@router.post("/{user_id}/deactivate")
async def deactivate_user(user_id: int):
    result = user_service.deactivate_user(user_id)
    if not result:
        return {"error": "User not found"}, 404
    return result
