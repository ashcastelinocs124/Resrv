import aiosqlite

from config import settings

_db: aiosqlite.Connection | None = None


async def init_db() -> aiosqlite.Connection:
    """Initialise the database: open connection, enable WAL, create tables."""
    global _db
    _db = await aiosqlite.connect(settings.database_path)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA foreign_keys=ON")
    await _create_tables(_db)
    await _migrate(_db)
    await _seed_machines(_db)
    await _db.commit()
    return _db


async def get_db() -> aiosqlite.Connection:
    """Return the active database connection."""
    if _db is None:
        raise RuntimeError("Database not initialised — call init_db() first")
    return _db


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


async def _create_tables(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS machines (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            name             TEXT    NOT NULL,
            slug             TEXT    UNIQUE NOT NULL,
            status           TEXT    NOT NULL DEFAULT 'active',
            embed_message_id TEXT,
            created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id   TEXT    UNIQUE NOT NULL,
            discord_name TEXT,
            email        TEXT    UNIQUE,
            verified     INTEGER NOT NULL DEFAULT 0,
            college      TEXT,
            major        TEXT,
            created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS queue_entries (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER NOT NULL REFERENCES users(id),
            machine_id     INTEGER NOT NULL REFERENCES machines(id),
            status         TEXT    NOT NULL DEFAULT 'waiting',
            position       INTEGER NOT NULL,
            joined_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            serving_at     TEXT,
            completed_at   TEXT,
            reminded       INTEGER NOT NULL DEFAULT 0,
            job_successful INTEGER,
            failure_notes  TEXT
        );

        CREATE TABLE IF NOT EXISTS verification_codes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id TEXT NOT NULL,
            email      TEXT NOT NULL,
            code       TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used       INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS analytics_snapshots (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            date           TEXT    NOT NULL,
            machine_id     INTEGER NOT NULL REFERENCES machines(id),
            total_jobs     INTEGER NOT NULL DEFAULT 0,
            completed_jobs INTEGER NOT NULL DEFAULT 0,
            avg_wait_mins  REAL,
            avg_serve_mins REAL,
            peak_hour      INTEGER,
            ai_summary     TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_queue_status
            ON queue_entries(status);
        CREATE INDEX IF NOT EXISTS idx_queue_machine_date
            ON queue_entries(machine_id, joined_at);
        """
    )


async def _migrate(db: aiosqlite.Connection) -> None:
    """Run lightweight migrations for schema changes."""
    # Add embed_message_id to machines if missing
    cursor = await db.execute("PRAGMA table_info(machines)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "embed_message_id" not in columns:
        await db.execute("ALTER TABLE machines ADD COLUMN embed_message_id TEXT")


async def _seed_machines(db: aiosqlite.Connection) -> None:
    """Insert the 4 SCD machines if they don't already exist."""
    machines = [
        ("Large Format Printer", "large-format-printer"),
        ("Laser Cutter", "laser-cutter"),
        ("CNC Router", "cnc-router"),
        ("Water Jet", "water-jet"),
    ]
    for name, slug in machines:
        await db.execute(
            "INSERT OR IGNORE INTO machines (name, slug) VALUES (?, ?)",
            (name, slug),
        )
