from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def _prepare_sqlite_url(database_path: Path) -> str:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{database_path.as_posix()}"


def create_sqlite_engine(database_path: Path) -> Engine:
    engine = create_engine(
        _prepare_sqlite_url(database_path),
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=FULL")
        cursor.execute("PRAGMA journal_mode=DELETE")
        cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def run_migrations(database_path: Path) -> None:
    config = Config(str(Path(__file__).parents[4] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", _prepare_sqlite_url(database_path))
    command.upgrade(config, "head")
