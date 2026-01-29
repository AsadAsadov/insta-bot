from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, Setting

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/insta_bot.db")

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        ensure_setting(session, "auto_reply_enabled", "true")


@contextmanager
def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ensure_setting(session: Session, key: str, value: str) -> None:
    existing = session.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    if existing is None:
        session.add(Setting(key=key, value=value))


def get_setting(session: Session, key: str, default: str | None = None) -> str | None:
    setting = session.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    if setting is None:
        return default
    return setting.value


def set_setting(session: Session, key: str, value: str) -> None:
    setting = session.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    if setting is None:
        session.add(Setting(key=key, value=value))
    else:
        setting.value = value
