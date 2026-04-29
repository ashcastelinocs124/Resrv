# Illinois Email Verification — Design

**Date:** 2026-04-29
**Status:** Approved, ready for implementation plan
**Goal:** Block queue joins until the user has proved control of an `@illinois.edu` mailbox by entering a 6-digit code we mailed them.

The `verification_codes` table and the `users.verified` column already exist; nothing has ever wired them up. Today the signup modal accepts any string matching `^.+@illinois\.edu$` regex and joins the queue immediately, so impersonation is trivial. This design closes the gap.

## Scope decisions (from brainstorming)

| Question | Decision |
|---|---|
| Strict / soft / opt-in verification? | **Strict** — hold the join until verified. |
| Transport | **SMTP** (defaults to Gmail `smtp.gmail.com:587` + Google App Password). |
| Provider | **Gmail** at deploy time, but config is generic SMTP env vars so UIUC / SES / Mailtrap work without code change. |
| Code format | **6-digit numeric**, **10-min lifetime**, entered via a **Discord modal**. |
| Per-user persistence | `users.verified=1` is sticky — verified user skips the flow on every future join. |
| Admin escape hatch | Existing `public_mode=true` setting bypasses verification entirely. |

## Architecture

### Schema
- **Reuse** `verification_codes(id, discord_id, email, code, expires_at, used)` — table already in `db/database.py`.
- **Reuse** `users.verified` — already `INTEGER NOT NULL DEFAULT 0`.
- **New (optional):** `users.verified_at TEXT NULL` via `_migrate` for audit. `ALTER TABLE` is additive on upgrades.

### Config (`config.py` + `.env`)

```python
smtp_host                       = "smtp.gmail.com"   # default
smtp_port                       = 587                # STARTTLS
smtp_username                   = ""                 # full Gmail address
smtp_password                   = ""                 # 16-char App Password
smtp_from                       = ""                 # defaults to smtp_username
verification_code_ttl_minutes   = 10
verification_max_codes_per_hour = 5                  # per discord_id
```

A missing `smtp_password` makes the verification helper return `None` from a lazy factory; calls degrade to a 503-style "service unavailable" message instead of crashing — same pattern as `_make_openai_client()` in chat/agent.

### Service module — `bot/email_verification.py`

Stdlib-style helpers, all `async`:

- `_make_smtp_client() -> aiosmtplib.SMTP | None` — lazy factory; returns `None` if creds missing.
- `issue_code(discord_id, email) -> str` — generates a 6-digit code, marks any prior unused codes for the same `discord_id` as `used=1`, inserts a new row with `expires_at = now + ttl`, enforces a 5-per-hour rate limit (raises `VerificationRateLimitError`).
- `send_verification_email(to_email, code) -> None` — `aiosmtplib.send` over STARTTLS:587. Subject: *"Your Reserv verification code"*. Plain-text body: the 6 digits + 10-min note. Wraps SMTP failures in `EmailSendError`.
- `verify_code(discord_id, code) -> tuple[bool, str | None]` — finds the latest unused, unexpired row for the discord_id; on match marks `used=1` + returns `(True, email)`; on miss bumps an attempts counter on the row; after 5 wrong attempts invalidates the row.
- `mark_user_verified(user_id, email) -> None` — `UPDATE users SET verified=1, email=?, verified_at=datetime('now')`.

### Discord flow — `bot/cogs/queue.py`

Today: click Join → `CollegeSelectView` → `SignupModal` → `register_user` → `join_queue` → DM.

The change inserts a verification step between `SignupModal` submit and `join_queue`, gated by `public_mode == "false"`.

```
SignupModal.submit
  ├─ regex check @illinois.edu  (existing)
  ├─ if public_mode == "true"            → existing fast path (no email)
  ├─ if user.verified == 1 AND email matches stored → existing fast path
  └─ else:
       ├─ persist profile (registered=0 still — locks them out of join)
       ├─ issue_code() + send_verification_email()
       ├─ open VerificationModal ephemerally
       │     "We sent a 6-digit code to user@illinois.edu. Enter it below.
       │      Didn't get it? Hit Cancel and click Join again."
       ├─ VerificationModal.submit
       │     ├─ verify_code() → on success:
       │     │     ├─ mark_user_verified()
       │     │     ├─ register_user()  (registered=1)
       │     │     ├─ join_queue()
       │     │     └─ confirmation DM (existing pattern, live #N)
       │     └─ on failure: ephemeral error, modal stays usable for retry
       └─ rate-limit hits → ephemeral "Too many requests, try again in N min"
```

The `existing` user path (`get_user_active_entry` → "already in queue") is unchanged. A user who's already verified never sees the verification modal again — `users.verified` is sticky.

Admin override: `public_mode=true` skips verification entirely (escape hatch for events). Same setting that's there now; no new toggle.

### Tests (~10 new)

`tests/test_email_verification.py` — DB layer + helpers, mocked SMTP:

1. `issue_code` returns a 6-digit numeric code.
2. New code invalidates the previous code for the same `discord_id`.
3. `verify_code` accepts the latest code, returns the stored email.
4. Expired code → `(False, None)`.
5. Wrong code 5× → row invalidated.
6. Rate-limit: 6th call within an hour raises `VerificationRateLimitError`.
7. SMTP factory returns `None` when creds missing → `send_verification_email` raises `EmailSendError`.

`tests/test_queue_signup_flow.py` — cog-level with monkeypatched SMTP send:

8. New unverified user goes through the verification modal before joining.
9. Already-verified user skips the verification modal.
10. `public_mode=true` skips verification regardless of `users.verified`.

### Failure modes

| Failure | User sees | Server logs |
|---|---|---|
| SMTP creds missing | "Verification is temporarily unavailable — please ask staff" | WARN: smtp not configured |
| SMTP send error | "Could not send code — try again in a minute" | EXCEPTION |
| Rate limit (5/hour) | "Too many code requests, try again in N minutes" | INFO |
| 5 wrong codes | "Too many wrong attempts — request a fresh code" | INFO |
| Code expired | "Code expired — please request a new one" | INFO |
| Invalid email format | (already handled by existing regex) | — |

### Out of scope (YAGNI)

- Click-to-resend button inside the verification modal — user just clicks Join again.
- Email change after verification — verified email is sticky; admin can flip `users.verified=0` via DB if needed.
- Verification audit dashboard — `verified_at` column is enough for grep-ability.

## Conventions to respect

- Lazy `_make_smtp_client()` factory matching the chat/agent OpenAI pattern (learnings.md 2026-04-02 / 2026-04-26). Missing creds degrade gracefully.
- Reuse `verification_codes` schema — additive `_migrate` only for `users.verified_at`.
- Discord modals follow the existing `SignupModal` / `FeedbackModal` pattern.
- New env vars feed `Settings` with `extra="ignore"` so unrelated env vars don't break startup (learnings.md 2026-04-22).
- Tests mock SMTP — never call a live mail server.

## Acceptance

Implementation is complete when:

1. A new user clicking Join sees the verification modal after submitting their signup info.
2. Entering the wrong code shows an error and lets them retry.
3. Entering the right code lands them in the queue with the existing confirmation DM (now showing live #N).
4. A second Join attempt by the same verified user goes straight to the queue, no email step.
5. With `public_mode=true`, verification is skipped entirely and the existing fast path runs.
6. SMTP outage produces a graceful "service unavailable" instead of a crash.
7. All ~10 new tests pass; full suite (currently 294) stays green; `npx tsc -b` clean (no frontend changes expected).
