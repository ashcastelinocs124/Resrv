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
    await _seed_colleges(_db)
    await _seed_staff(_db)
    await _seed_settings(_db)
    await _backfill_main_units(_db)
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

        CREATE TABLE IF NOT EXISTS machine_units (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id   INTEGER NOT NULL REFERENCES machines(id),
            label        TEXT    NOT NULL,
            status       TEXT    NOT NULL DEFAULT 'active',
            created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
            archived_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS colleges (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            archived_at TEXT
        );

        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id   TEXT    UNIQUE NOT NULL,
            discord_name TEXT,
            email        TEXT    UNIQUE,
            verified     INTEGER NOT NULL DEFAULT 0,
            college_id   INTEGER REFERENCES colleges(id),
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

        CREATE TABLE IF NOT EXISTS chat_conversations (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_user_id INTEGER NOT NULL REFERENCES staff_users(id),
            title         TEXT    NOT NULL DEFAULT 'New chat',
            created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS chat_messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
            role            TEXT    NOT NULL CHECK (role IN ('user','assistant','system','tool')),
            content         TEXT    NOT NULL,
            tool_call_id    TEXT,
            tool_calls_json TEXT,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
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

    # machine_units may be missing on upgrades from pre-multi-unit DBs.
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS machine_units (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id   INTEGER NOT NULL REFERENCES machines(id),
            label        TEXT    NOT NULL,
            status       TEXT    NOT NULL DEFAULT 'active',
            created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
            archived_at  TEXT
        )
        """
    )
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_machine_units_label_active "
        "ON machine_units(machine_id, label) WHERE archived_at IS NULL"
    )

    # Add queue_entries.unit_id if missing
    cursor = await db.execute("PRAGMA table_info(queue_entries)")
    qe_cols = {row[1] for row in await cursor.fetchall()}
    if "unit_id" not in qe_cols:
        await db.execute(
            "ALTER TABLE queue_entries "
            "ADD COLUMN unit_id INTEGER REFERENCES machine_units(id)"
        )

    # Backfill: every non-archived machine with zero units gets a "Main" unit.
    # Runs here for upgrade paths (where existing machines predate units).
    # Also re-invoked after _seed_machines in init_db so seeded machines are covered.
    await _backfill_main_units(db)

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

    # Colleges table — may be missing on upgrades.
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS colleges (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            archived_at TEXT
        )
        """
    )
    # Partial unique index AFTER the table exists (learnings.md 2026-04-22).
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_colleges_name_active "
        "ON colleges(name) WHERE archived_at IS NULL"
    )

    # Add users.college_id (nullable FK).
    cursor = await db.execute("PRAGMA table_info(users)")
    user_cols_v2 = {row[1] for row in await cursor.fetchall()}
    if "college_id" not in user_cols_v2:
        await db.execute(
            "ALTER TABLE users ADD COLUMN college_id INTEGER REFERENCES colleges(id)"
        )

    # Drop legacy users.college (free-text, replaced by FK). Safe on SQLite >= 3.35.
    if "college" in user_cols_v2:
        await db.execute("ALTER TABLE users DROP COLUMN college")

    # Re-signup wipe: any user previously marked registered=1 must re-pick a college.
    # Idempotent — once flipped to 0 they no longer match the predicate.
    await db.execute("UPDATE users SET registered = 0 WHERE registered = 1")

    # Chat tables (analytics chatbot) — additive on upgrade.
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_conversations (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_user_id INTEGER NOT NULL REFERENCES staff_users(id),
            title         TEXT    NOT NULL DEFAULT 'New chat',
            created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
            role            TEXT    NOT NULL CHECK (role IN ('user','assistant','system','tool')),
            content         TEXT    NOT NULL,
            tool_call_id    TEXT,
            tool_calls_json TEXT,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_msgs_conv "
        "ON chat_messages(conversation_id, id)"
    )


async def _backfill_main_units(db: aiosqlite.Connection) -> None:
    """Ensure every non-archived machine has at least one active unit.

    Idempotent: only inserts a 'Main' unit for machines that currently have
    zero non-archived units. Called from _migrate (for upgrade paths) and
    again from init_db after _seed_machines (so seeded machines are covered).
    """
    await db.execute(
        """
        INSERT INTO machine_units (machine_id, label)
        SELECT m.id, 'Main'
        FROM machines m
        WHERE m.archived_at IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM machine_units u
              WHERE u.machine_id = m.id AND u.archived_at IS NULL
          )
        """
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


async def _seed_colleges(db: aiosqlite.Connection) -> None:
    """Seed the standard UIUC colleges if missing. Idempotent."""
    colleges = [
        "Grainger College of Engineering",
        "Gies College of Business",
        "College of Liberal Arts and Sciences",
        "College of Agricultural, Consumer and Environmental Sciences",
        "College of Education",
        "College of Fine and Applied Arts",
        "College of Media",
        "School of Information Sciences",
        "College of Applied Health Sciences",
        "Division of General Studies",
        "School of Social Work",
        "School of Labor and Employment Relations",
        "Carle Illinois College of Medicine",
        "College of Veterinary Medicine",
        "College of Law",
    ]
    for name in colleges:
        await db.execute(
            """
            INSERT INTO colleges (name)
            SELECT ?
            WHERE NOT EXISTS (
                SELECT 1 FROM colleges WHERE name = ? AND archived_at IS NULL
            )
            """,
            (name, name),
        )
