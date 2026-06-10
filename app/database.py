from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _is_mssql(url: str) -> bool:
    return "mssql" in url or "pyodbc" in url


_connect_args = {"check_same_thread": False} if _is_sqlite(settings.DATABASE_URL) else {}

_engine_kwargs = {
    "connect_args": _connect_args,
    "pool_pre_ping": True,
}
if not _is_sqlite(settings.DATABASE_URL):
    _engine_kwargs["pool_size"] = settings.DB_POOL_SIZE
    _engine_kwargs["max_overflow"] = settings.DB_MAX_OVERFLOW
if _is_mssql(settings.DATABASE_URL):
    _connect_args = _engine_kwargs.get("connect_args", {})
    _connect_args.setdefault("TrustServerCertificate", "yes")
    _engine_kwargs["connect_args"] = _connect_args

engine = create_engine(settings.DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
