"""Notification provider domain."""

from omniagents.core.providers.notification.base import NotificationProvider
from omniagents.core.providers.notification.none import NoneNotification
from omniagents.core.providers.notification.webhook import WebhookNotification
from omniagents.core.providers import register_provider

register_provider("notification", "none", NoneNotification)
register_provider("notification", "webhook", WebhookNotification)

__all__ = ["NotificationProvider", "NoneNotification", "WebhookNotification"]
