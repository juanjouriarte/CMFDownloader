from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from src.config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=int(os.getenv("DB_POOL_SIZE", "3")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "2")),
    pool_recycle=int(os.getenv("DB_POOL_RECYCLE_SECONDS", "1800")),
    pool_timeout=int(os.getenv("DB_POOL_TIMEOUT_SECONDS", "10")),
    connect_args={
        "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT_SECONDS", "5")),
        "application_name": os.getenv("DB_APPLICATION_NAME", "cmfdownloader"),
        "options": (
            f"-c statement_timeout="
            f"{int(os.getenv('DB_STATEMENT_TIMEOUT_MS', '0'))}"
        ),
    },
)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass
