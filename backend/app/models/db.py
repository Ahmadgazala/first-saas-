"""Database engine/session setup (PostgreSQL in production, any SQLAlchemy URL in tests)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str):
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs = {"connect_args": {"check_same_thread": False}}
        self.engine: Engine = create_engine(url, **kwargs)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        from app.models import entities  # noqa: F401  (register models)

        Base.metadata.create_all(self.engine)
        self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        """Minimal forward migration: add nullable/defaulted columns introduced after a
        table was first created (create_all never alters existing tables)."""
        from sqlalchemy import inspect, text

        inspector = inspect(self.engine)
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                if not inspector.has_table(table.name):
                    continue
                existing = {c["name"] for c in inspector.get_columns(table.name)}
                for column in table.columns:
                    if column.name in existing:
                        continue
                    ddl_type = column.type.compile(dialect=self.engine.dialect)
                    default = column.server_default.arg if column.server_default is not None else None
                    clause = f" DEFAULT '{default}'" if default is not None else ""
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {column.name} {ddl_type}{clause}'))

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
