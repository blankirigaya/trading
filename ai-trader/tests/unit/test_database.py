"""SQLite contention settings: WAL mode + long busy timeout."""



def test_wal_and_busy_timeout(tmp_path) -> None:
    from ai_trader.persistence.database import create_database_engine

    engine = create_database_engine(f"sqlite:///{tmp_path / 'c.db'}")
    with engine.connect() as conn:
        mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
        assert str(mode).upper() == "WAL"
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 30000


def test_non_sqlite_urls_untouched() -> None:
    from ai_trader.persistence import database

    seen = {}

    class FakeEngine:
        pass

    real_create = database.create_engine
    try:
        database.create_engine = lambda url: seen.setdefault("url", url) or FakeEngine()  # type: ignore[assignment]
        database.create_database_engine("postgresql://u:p@h/db")
    finally:
        database.create_engine = real_create
    assert seen["url"] == "postgresql://u:p@h/db"
