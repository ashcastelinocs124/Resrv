# Learnings

### 2026-04-26 — Stale `position` column in queue_entries
**Ref:** Architecture > web/src/components/QueueCard.tsx, MachineColumn.tsx
- **What:** `queue_entries.position` is stamped at join time and never renumbered. After two earlier entries finish, the third user still reads as `#3` even though they're effectively first in line. Discord embed already enumerated `enumerate(waiting, start=1)`; only the web card was wrong.
- **Why it matters:** Users see a stale rank that contradicts reality. Server-side renumbering on every promotion/leave would cost a write per mutation.
- **Fix/Pattern:** Compute display rank in the *parent* component (`MachineColumn` filters waiting + indexes), pass it to `QueueCard` as `displayPosition` prop. Keep `entry.position` for ordering only. Serving entries render "serving" instead of a number.

### 2026-04-26 — SSE chat streaming uses fetch + ReadableStream, not EventSource
**Ref:** Architecture > web/src/api/client.ts > postChatStream
- **What:** EventSource is the obvious primitive for SSE in the browser, but it can't carry custom headers (no Authorization). The chat API is gated by Bearer token, so EventSource is unusable. Use `fetch()` with `Accept: text/event-stream` and read `response.body` as a ReadableStream, decoding chunks and splitting on `\n\n`.
- **Why it matters:** Picking EventSource on a token-auth endpoint either forces auth into the URL (terrible — query strings get logged) or a cookie session (mismatch with our Bearer flow). fetch streaming is the only clean path for the existing auth model.
- **Fix/Pattern:** `fetch(url, {method:"POST", headers:{Accept:"text/event-stream", Authorization:"Bearer …"}})`, then `response.body.getReader()` + `TextDecoder({stream:true})` to handle UTF-8 across chunk boundaries. Buffer until a blank line, parse `data: <json>` payloads, dispatch event-type handlers.

### 2026-04-26 — Mocking OpenAI streaming requires an `__aiter__` stub
**Ref:** Architecture > tests/test_chat_api.py > mock_openai_stream
- **What:** Non-streaming `chat.completions.create` returns an awaitable that yields a `ChatCompletion`-shaped object with `.choices[0].message.content`. The streaming variant returns an async-iterable that yields chunks with `.choices[0].delta.content`. A non-streaming mock breaks streaming tests.
- **Why it matters:** Easy to write a single `mock_openai` fixture that "looks right" but blows up the moment a route asks for `stream=True`.
- **Fix/Pattern:** Two fixtures. Streaming fixture returns an object whose `__aiter__` yields a sequence of `SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=piece))])`. Assert `kwargs.get("stream") is True` inside the mock so the wrong fixture fails loudly.

### 2026-04-26 — User-selectable model needs a server-side allowlist
**Ref:** Architecture > api/routes/chat.py > ALLOWED_MODELS
- **What:** A frontend dropdown that posts `model: <string>` straight to OpenAI lets any logged-in user invoke any model the org has access to — including expensive ones — by hand-crafting a request.
- **Why it matters:** Cost surface and prompt-injection surface both grow with the model list. A trimmed allowlist is the only way to keep both bounded.
- **Fix/Pattern:** Maintain `ALLOWED_MODELS` (id + label) in the chat router. `_resolve_model(requested)` returns the default if `None`, raises 400 if the requested string isn't in the set. `GET /chat/models` returns the same list so the frontend dropdown can't drift from the server's view of what's permissible.

### 2026-04-22 — Pydantic Settings rejects unknown .env keys by default
**Ref:** Architecture > config.py
- **What:** After the `.env` picked up an unrelated `HETZNER_TOKEN`, `Settings()` began raising `ValidationError: extra_forbidden` at import time, blocking all tests and app startup.
- **Why it matters:** pydantic-settings v2 defaults to `extra="forbid"`. Any drive-by env var unrelated to the Settings class will crash the app.
- **Fix/Pattern:** Set `model_config = {..., "extra": "ignore"}` on the Settings class so unrelated env vars are skipped instead of blocking startup.

### 2026-04-22 — Soft-deleted parent with FK-referencing children blocks purge
**Ref:** Architecture > db/models.py > purge_machine
- **What:** After adding `machine_units(machine_id REFERENCES machines(id))`, `purge_machine` started failing with `FOREIGN KEY constraint failed` because the children outlived the parent delete.
- **Why it matters:** Any new table that FK-references an existing purgable parent has to be added to the cascade-delete path, or the parent delete will fail at the last step.
- **Fix/Pattern:** When adding a new child table, grep for existing `purge_*` / `DELETE FROM <parent>` helpers and add `DELETE FROM <child> WHERE <fk> = ?` before the parent delete.

### 2026-04-22 — Backfill migrations must run AFTER seed steps, not inside `_migrate`
**Ref:** Architecture > db/database.py > init_db ordering
- **What:** Task 1 plan placed the machine_units "Main"-unit backfill INSERT inside `_migrate`. In Reserv's actual `init_db` order (create → migrate → seed_machines → seed_staff → seed_settings), `_migrate` runs BEFORE `_seed_machines`, so on a fresh DB the backfill finds zero machines and the tests fail.
- **Why it matters:** Plans authored around an assumed call order silently break when the real order differs. Reordering `init_db` to move migrate after seeds is risky on upgrade DBs (seed_staff inserts with `role`, which migrate may need to add first).
- **Fix/Pattern:** Keep structural migrations (CREATE TABLE / ALTER / CREATE INDEX) in `_migrate` for upgrade safety, but extract data backfills into a separate helper (e.g. `_backfill_main_units`) and call it BOTH from `_migrate` (for upgrade paths) AND from `init_db` after `_seed_machines` (for fresh paths). Make the backfill idempotent via `INSERT ... WHERE NOT EXISTS` so double-invocation is a no-op.

### 2026-04-22 — Partial unique indexes must be created post-ALTER, not in CREATE TABLE block
**Ref:** Architecture > db/database.py > _migrate
- **What:** `CREATE UNIQUE INDEX ... WHERE archived_at IS NULL` inside `_create_tables` fails on upgrades because `CREATE TABLE IF NOT EXISTS` is a no-op on the existing (pre-archived_at) table; the index then references a column that won't exist until `_migrate` adds it.
- **Why it matters:** Breaks app startup on prod DBs after any soft-delete migration. Tests passed because in-memory SQLite always ran fresh CREATE TABLE.
- **Fix/Pattern:** Put partial unique indexes in `_migrate`, after the ALTER TABLE that adds the column they depend on. Use `CREATE INDEX IF NOT EXISTS` so the migration stays idempotent.

### 2026-04-22 — Soft-delete + slug reuse needs partial index, not column-level UNIQUE
**Ref:** Architecture > db/database.py > machines table
- **What:** `slug TEXT UNIQUE NOT NULL` at the column level blocks reusing a slug after archive, even though semantically the archived row is "gone". Switched to `slug TEXT NOT NULL` + a partial unique index `ON machines(slug) WHERE archived_at IS NULL`.
- **Why it matters:** Without this, admins couldn't recreate a machine with the same slug after archiving one. The lesson: column-level UNIQUE is the wrong tool when soft-delete is on the table.
- **Fix/Pattern:** For any soft-deleted table, push uniqueness into a `WHERE deleted_at IS NULL` partial index.

### 2026-04-22 — Staff auth added (public vs. staff split)
**Ref:** Architecture > api/auth.py, api/routes/auth.py
- **What:** Added stdlib-only staff auth: PBKDF2 password hashing + HMAC-signed tokens (no JWT/bcrypt deps). `require_staff` dependency attached at the analytics router, leaving machines/queue routes public.
- **Why it matters:** Students see queues without logging in; staff must log in to see analytics. Default creds come from `settings.staff_username`/`staff_password` and seed on first run if `staff_users` is empty.
- **Fix/Pattern:** Change the default creds by setting `STAFF_USERNAME`/`STAFF_PASSWORD`/`AUTH_SECRET` in `.env` before first startup. Token is stored in `localStorage` under `reserv.auth.token`; API client auto-attaches `Authorization: Bearer` and clears token on 401.

### 2026-04-01 -- discord.py persistent views require timeout=None and custom_id
- **What:** For buttons to survive bot restarts, the View must use `timeout=None` and each Button must have an explicit `custom_id`. The view must also be re-registered via `bot.add_view()` in `setup_hook`.
- **Why it matters:** Without this, buttons stop working after the bot process restarts -- a common pitfall in discord.py bot development.
- **Fix/Pattern:** Use `QueueButtonView(machine_id)` with `custom_id=f"action:{machine_id}"`, register in `setup_hook`, handle via `on_interaction` listener checking `custom_id`.

### 2026-04-01 -- discord.py cog loading uses extension paths not file paths
- **What:** `bot.load_extension("bot.cogs.queue")` uses Python dotted module paths, not file paths. Each cog module needs an `async def setup(bot)` function at module level.
- **Why it matters:** Easy to confuse with file paths; wrong format silently fails or raises confusing errors.
- **Fix/Pattern:** Always use dotted module path, always include `async def setup(bot)` at bottom of cog file.

### 2026-04-01 -- tasks.loop tick rate reads settings at import time
- **What:** `@tasks.loop(seconds=settings.agent_tick_seconds)` evaluates the seconds parameter when the decorator runs (import time), not when the loop starts. Changing the setting after import has no effect on the loop interval.
- **Why it matters:** If you need dynamic intervals, you'd need to cancel and restart the loop. For MVP this is fine since config is loaded once from .env.
- **Fix/Pattern:** Accept that loop interval is fixed at import time; document this if dynamic intervals are ever needed.

### 2026-04-02 -- DM cog Views use timeout=60 (not None) since they are ephemeral
- **What:** Unlike the persistent `QueueButtonView` (timeout=None), the DM cog's `MachinePicker` and `FallbackActions` views use `timeout=60` because they are sent as one-off DM replies, not pinned channel embeds. They don't need to survive restarts.
- **Why it matters:** Using `timeout=None` on ephemeral views would leak memory since discord.py keeps them alive indefinitely. Using a reasonable timeout ensures cleanup.
- **Fix/Pattern:** Persistent channel embeds -> `timeout=None` + `bot.add_view()`. Ephemeral DM replies -> `timeout=60` (or appropriate duration).

### 2026-04-02 -- OpenAI client created lazily to avoid import-time failures
- **What:** The `AsyncOpenAI` client is created via a factory function `_make_openai_client()` rather than at import time. If the openai package is missing or no API key is configured, the cog degrades gracefully (always returns "unknown" intent, showing fallback buttons).
- **Why it matters:** Hard import of `openai` at module level would crash the bot if the package isn't installed or key isn't set. Lazy creation with try/except makes the dependency optional.
- **Fix/Pattern:** Wrap optional dependency imports in try/except and use None-checks throughout.

### 2026-04-02 -- DM cog intents need message_content intent enabled on bot
- **What:** The bot currently uses `Intents.default()` which does NOT include `message_content`. For the DM cog's `on_message` listener to receive message content in DMs, the `message_content` intent must be enabled. This is handled in Task 4 (wiring).
- **Why it matters:** Without the intent, `message.content` will be empty and classification will always fail.
- **Fix/Pattern:** Enable `intents.message_content = True` in `ReservBot.__init__` and ensure the intent is toggled on in the Discord Developer Portal.

### 2026-04-01 — aiosqlite Row factory + dict() pattern
- **What:** Setting `_db.row_factory = aiosqlite.Row` lets you access columns by name, and `dict(row)` converts to a plain dict. This avoids needing an ORM while keeping ergonomic data access.
- **Why it matters:** Raw tuples are error-prone and positional. Row objects support both `row["col"]` and `dict(row)` conversion.
- **Fix/Pattern:** Always set `row_factory = aiosqlite.Row` after connecting, wrap results with `dict()` in helper functions.
