"""Application configuration loaded from environment variables."""

import os


APP_ENV = os.getenv("APP_ENV", "production")
API_KEY = os.getenv("API_KEY", "dev-api-key-123")
APP_VERSION = "0.1.0"
