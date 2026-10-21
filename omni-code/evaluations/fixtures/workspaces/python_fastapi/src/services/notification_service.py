"""Notification stubs for email and webhook alerts."""

from typing import Optional


_sent_notifications: list = []


def notify_new_comment(post_title: str, commenter_name: str) -> None:
    _sent_notifications.append({
        "type": "new_comment",
        "post_title": post_title,
        "commenter": commenter_name,
    })


def notify_post_published(post_title: str, author_name: str) -> None:
    _sent_notifications.append({
        "type": "post_published",
        "post_title": post_title,
        "author": author_name,
    })


def get_sent_notifications() -> list:
    return list(_sent_notifications)


def clear_notifications() -> None:
    _sent_notifications.clear()
