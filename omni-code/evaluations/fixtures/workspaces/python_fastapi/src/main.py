"""FastAPI application entry point."""

from fastapi import FastAPI

from src.middleware.auth import AuthMiddleware
from src.routes import comment_routes, post_routes, user_routes

app = FastAPI(title="Demo API", version="0.1.0")
app.add_middleware(AuthMiddleware)
app.include_router(user_routes.router)
app.include_router(post_routes.router)
app.include_router(comment_routes.router)


@app.get("/")
async def root():
    return {"message": "Hello, World!"}


@app.get("/health")
async def health():
    return {"status": "ok"}
