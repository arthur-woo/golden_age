"""
Golden Age - Local Settings

로컬 개발 환경 설정.
.env 파일에서 환경변수를 읽는다.
"""

import environ
from pathlib import Path

from .base import *  # noqa: F401, F403

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()

# .env 파일 로드 (없어도 무방 - 환경변수 직접 설정 가능)
env.read_env(BASE_DIR.parent / "docker" / ".env", overwrite=False)

SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-local-dev-key-change-in-production")

DEBUG = True

ALLOWED_HOSTS = ["*"]

# Database
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgresql://golden_age:golden_age@localhost:5432/golden_age",
    )
}
