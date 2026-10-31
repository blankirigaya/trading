"""SQLite/Postgres engine factory with contention-proof SQLite settings.

Failure mode this exists for: under concurrent readers and writers the default
rollback-journal SQLite raised ``database is locked`` after a ~5s busy timeout,
crashing news ingestion and every journal-backed API endpoint. WAL mode lets
readers never block writers; a 30s busy timeout absorbs write spikes instead
of failing them. Pure configuration: no call site changes.
"""

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine


def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_database_engine(database_url: str) -> Engine:
    engine = create_engine(database_url)
    if database_url.startswith("sqlite"):
        event.listen(engine, "connect", _sqlite_pragmas)
    return engine
