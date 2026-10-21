"""User business logic."""

from typing import Optional

from src import database


def validate_email(email: str) -> bool:
    return "@" in email and "." in email.split("@")[-1]


def create_user(name: str, email: str, bio: Optional[str] = None) -> dict:
    if not validate_email(email):
        raise ValueError(f"Invalid email: {email}")
    if database.query("users", email=email):
        raise ValueError(f"Email already registered: {email}")
    return database.insert("users", {"name": name, "email": email, "bio": bio, "is_active": True})


def get_user(user_id: int) -> Optional[dict]:
    return database.get("users", user_id)


def list_users() -> list:
    return database.get_all("users")


def deactivate_user(user_id: int) -> Optional[dict]:
    return database.update("users", user_id, {"is_active": False})


def format_user_greeting(name: str) -> str:
    return f"Welcome, {name.title()}!"
