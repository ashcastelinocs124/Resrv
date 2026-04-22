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
    await _seed_staff(_db)
    await _seed_settings(_db)
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
            slug             TEXT    NOT NULL,
            status           TEXT    NOT NULL DEFAULT 'active',
            embed_message_id TEXT,
            created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
            archived_at      TEXT
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

        CREATE TABLE IF NOT EXISTS staff_users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    UNIQUE NOT NULL,
            password_hash TEXT    NOT NULL,
            role          TEXT    NOT NULL DEFAULT 'staff',
            created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
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

        CREATE TABLE IF NOT EXISTS settings (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
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
    if "archived_at" not in columns:
        await db.execute("ALTER TABLE machines ADD COLUMN archived_at TEXT")

    # Partial unique index on slug among active (non-archived) machines.
    # Must run after archived_at exists.
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_machines_slug_active "
        "ON machines(slug) WHERE archived_at IS NULL"
    )

    # Add role to staff_users if missing
    cursor = await db.execute("PRAGMA table_info(staff_users)")
    staff_columns = {row[1] for row in await cursor.fetchall()}
    if "role" not in staff_columns:
        await db.execute(
            "ALTER TABLE staff_users ADD COLUMN role TEXT NOT NULL DEFAULT 'staff'"
        )

    # Ensure at least one admin exists when staff rows are present
    row = await (await db.execute(
        "SELECT COUNT(*) AS cnt FROM staff_users WHERE role = 'admin'"
    )).fetchone()
    admin_count = row[0]
    row = await (await db.execute(
        "SELECT COUNT(*) AS cnt FROM staff_users"
    )).fetchone()
    total_count = row[0]
    if total_count > 0 and admin_count == 0:
        await db.execute(
            "UPDATE staff_users SET role = 'admin' "
            "WHERE id = (SELECT MIN(id) FROM staff_users)"
        )

    # Add signup fields to users if missing
    cursor = await db.execute("PRAGMA table_info(users)")
    user_columns = {row[1] for row in await cursor.fetchall()}
    if "full_name" not in user_columns:
        await db.execute("ALTER TABLE users ADD COLUMN full_name TEXT")
    if "graduation_year" not in user_columns:
        await db.execute("ALTER TABLE users ADD COLUMN graduation_year TEXT")
    if "registered" not in user_columns:
        await db.execute(
            "ALTER TABLE users ADD COLUMN registered INTEGER NOT NULL DEFAULT 0"
        )

    # Add role to staff_users if missing; backfill first (oldest) user as admin
    cursor = await db.execute("PRAGMA table_info(staff_users)")
    staff_columns = {row[1] for row in await cursor.fetchall()}
    if "role" not in staff_columns:
        await db.execute(
            "ALTER TABLE staff_users ADD COLUMN role TEXT NOT NULL DEFAULT 'staff'"
        )
        await db.execute(
            "UPDATE staff_users SET role = 'admin' "
            "WHERE id = (SELECT MIN(id) FROM staff_users)"
        )

    # Add new analytics columns if missing
    cursor = await db.execute("PRAGMA table_info(analytics_snapshots)")
    snap_columns = {row[1] for row in await cursor.fetchall()}
    if "no_show_count" not in snap_columns:
        await db.execute(
            "ALTER TABLE analytics_snapshots ADD COLUMN no_show_count INTEGER NOT NULL DEFAULT 0"
        )
    if "cancelled_count" not in snap_columns:
        await db.execute(
            "ALTER TABLE analytics_snapshots ADD COLUMN cancelled_count INTEGER NOT NULL DEFAULT 0"
        )
    if "unique_users" not in snap_columns:
        await db.execute(
            "ALTER TABLE analytics_snapshots ADD COLUMN unique_users INTEGER NOT NULL DEFAULT 0"
        )
    if "failure_count" not in snap_columns:
        await db.execute(
            "ALTER TABLE analytics_snapshots ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0"
        )


async def _seed_staff(db: aiosqlite.Connection) -> None:
    """Seed the default staff user from env if staff_users is empty."""
    from api.auth import hash_password

    cursor = await db.execute("SELECT COUNT(*) AS cnt FROM staff_users")
    row = await cursor.fetchone()
    if row[0] > 0:
        return
    username = settings.staff_username
    password = settings.staff_password
    if not username or not password:
        return
    await db.execute(
        "INSERT INTO staff_users (username, password_hash, role) VALUES (?, ?, ?)",
        (username, hash_password(password), "admin"),
    )


async def _seed_settings(db: aiosqlite.Connection) -> None:
    """Insert default runtime settings if missing."""
    defaults = {
        "reminder_minutes":   str(settings.reminder_minutes),
        "grace_minutes":      str(settings.grace_minutes),
        "queue_reset_hour":   str(settings.queue_reset_hour),
        "agent_tick_seconds": str(settings.agent_tick_seconds),
        "public_mode":        "false",
        "maintenance_banner": "",
    }
    for key, value in defaults.items():
        await db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )


async def _seed_machines(db: aiosqlite.Connection) -> None:
    """Insert the SCD machines if they don't already exist."""
    machines = [
        ("Large Format Printer", "large-format-printer"),
        ("Laser Cutter", "laser-cutter"),
        ("CNC Router", "cnc-router"),
        ("Water Jet", "water-jet"),
        ("3D Printer", "3d-printer"),
        ("Sewing Machine", "sewing-machine"),
    ]
    for name, slug in machines:
        await db.execute(
            "INSERT OR IGNORE INTO machines (name, slug) VALUES (?, ?)",
            (name, slug),
        )
