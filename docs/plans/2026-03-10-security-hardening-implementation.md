# Security & Reliability Hardening — Phase 1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix critical security and operational issues before internet-exposed production deployment.

**Architecture:** Seven independent hardening changes to the existing FastAPI fulfillment queue app. Each task is self-contained with its own tests. No new modules — all changes modify existing files.

**Tech Stack:** Python 3.12+, FastAPI, SQLite, passlib[bcrypt], itsdangerous, reportlab

---

### Task 1: Password Hashing with bcrypt

**Files:**
- Modify: `src/fulfillment/auth.py`
- Modify: `pyproject.toml` (add passlib[bcrypt] dependency)
- Test: `tests/test_auth.py` (create)

**Context:** Passwords are currently stored as plain text in the `settings` table via `db.set_setting("picker_password", "secret123")` and compared with `==` in `api.py:93`. We need to hash on storage and use constant-time verify on login. We also need a migration path: on first check, if a stored password isn't a bcrypt hash, hash it in place.

**Step 1: Add passlib dependency**

In `pyproject.toml`, add `"passlib[bcrypt]>=1.7.0"` to the `dependencies` list (after `"reportlab>=4.0.0"`).

Then run:
```bash
cd /home/cruz/alliance-fulfillment && /home/cruz/.local/bin/uv sync
```

**Step 2: Write failing tests**

Create `tests/test_auth.py`:

```python
import pytest
from fulfillment.auth import hash_password, verify_password, is_bcrypt_hash


def test_hash_password_returns_bcrypt_string():
    hashed = hash_password("mysecret")
    assert hashed.startswith("$2b$")
    assert hashed != "mysecret"


def test_verify_password_correct():
    hashed = hash_password("mysecret")
    assert verify_password("mysecret", hashed) is True


def test_verify_password_wrong():
    hashed = hash_password("mysecret")
    assert verify_password("wrongpassword", hashed) is False


def test_verify_password_empty_stored_returns_false():
    assert verify_password("anything", "") is False


def test_is_bcrypt_hash_true():
    hashed = hash_password("test")
    assert is_bcrypt_hash(hashed) is True


def test_is_bcrypt_hash_false_plain_text():
    assert is_bcrypt_hash("plaintext123") is False


def test_is_bcrypt_hash_false_empty():
    assert is_bcrypt_hash("") is False
```

**Step 3: Run tests to verify they fail**

Run: `cd /home/cruz/alliance-fulfillment && /home/cruz/.local/bin/uv run pytest tests/test_auth.py -v`
Expected: FAIL — `hash_password`, `verify_password`, `is_bcrypt_hash` do not exist yet.

**Step 4: Implement password hashing functions**

Add to `src/fulfillment/auth.py` (at the top, after existing imports):

```python
from passlib.hash import bcrypt

def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hash(password)

def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against a bcrypt hash. Returns False for empty hash."""
    if not stored_hash:
        return False
    return bcrypt.verify(password, stored_hash)

def is_bcrypt_hash(value: str) -> bool:
    """Check if a string looks like a bcrypt hash."""
    return value.startswith("$2b$") and len(value) >= 59
```

**Step 5: Run tests to verify they pass**

Run: `cd /home/cruz/alliance-fulfillment && /home/cruz/.local/bin/uv run pytest tests/test_auth.py -v`
Expected: All 7 tests PASS.

**Step 6: Commit**

```bash
cd /home/cruz/alliance-fulfillment
git add pyproject.toml src/fulfillment/auth.py tests/test_auth.py
git commit -m "feat(security): add bcrypt password hashing functions"
```

---

### Task 2: Wire Password Hashing into Auth Endpoints

**Files:**
- Modify: `src/fulfillment/api.py:88-108` (auth endpoints)
- Modify: `src/fulfillment/api.py:262-268` (settings endpoint)
- Modify: `tests/test_api.py`

**Context:** Now that `hash_password`, `verify_password`, and `is_bcrypt_hash` exist, we need to:
1. Hash passwords when they're saved via `/api/settings`
2. Use `verify_password` instead of `==` in `/api/auth/picker` and `/api/auth/manager`
3. Auto-migrate plain-text passwords to bcrypt on first verify attempt

**Step 1: Write failing tests**

Add to `tests/test_api.py` (at the end):

```python
def test_password_stored_as_bcrypt_hash(db):
    """Setting a password stores it as a bcrypt hash, not plain text."""
    app = create_app(db)
    c = TestClient(app)
    c.post("/api/settings", json={"key": "picker_password", "value": "secret123"})
    stored = db.get_setting("picker_password", "")
    assert stored.startswith("$2b$")
    assert stored != "secret123"


def test_login_works_with_hashed_password(db):
    """Login succeeds when password is stored as bcrypt hash."""
    from fulfillment.auth import hash_password
    db.set_setting("picker_password", hash_password("secret123"))
    app = create_app(db)
    c = TestClient(app)
    resp = c.post("/api/auth/picker", json={"password": "secret123"})
    assert resp.status_code == 200


def test_login_fails_with_wrong_password_hashed(db):
    """Login fails with wrong password when stored as bcrypt hash."""
    from fulfillment.auth import hash_password
    db.set_setting("picker_password", hash_password("secret123"))
    app = create_app(db)
    c = TestClient(app)
    resp = c.post("/api/auth/picker", json={"password": "wrong"})
    assert resp.status_code == 401


def test_plain_text_password_auto_migrated(db):
    """Plain-text password is auto-migrated to bcrypt on first login."""
    db.set_setting("picker_password", "oldplaintext")
    app = create_app(db)
    c = TestClient(app)
    resp = c.post("/api/auth/picker", json={"password": "oldplaintext"})
    assert resp.status_code == 200
    # After login, password should now be hashed
    stored = db.get_setting("picker_password", "")
    assert stored.startswith("$2b$")
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/cruz/alliance-fulfillment && /home/cruz/.local/bin/uv run pytest tests/test_api.py::test_password_stored_as_bcrypt_hash tests/test_api.py::test_login_works_with_hashed_password tests/test_api.py::test_login_fails_with_wrong_password_hashed tests/test_api.py::test_plain_text_password_auto_migrated -v`
Expected: FAIL — passwords not yet hashed.

**Step 3: Implement**

In `src/fulfillment/api.py`, add import at the top (after existing `from fulfillment.auth import ...`):

```python
from fulfillment.auth import make_serializer, set_auth_cookie, check_auth, require_password_set, hash_password, verify_password, is_bcrypt_hash
```

Replace the `auth_picker` endpoint (lines 88-97) with:

```python
    @app.post("/api/auth/picker")
    async def auth_picker(request: Request):
        body = await request.json()
        password = body.get("password", "")
        stored = db.get_setting("picker_password", "")
        if stored == "":
            resp = JSONResponse({"status": "ok"})
            set_auth_cookie(resp, serializer, "picker")
            return resp
        if is_bcrypt_hash(stored):
            if verify_password(password, stored):
                resp = JSONResponse({"status": "ok"})
                set_auth_cookie(resp, serializer, "picker")
                return resp
        else:
            # Plain-text migration: verify and hash in place
            if password == stored:
                db.set_setting("picker_password", hash_password(stored))
                resp = JSONResponse({"status": "ok"})
                set_auth_cookie(resp, serializer, "picker")
                return resp
        return JSONResponse({"error": "wrong password"}, status_code=401)
```

Replace the `auth_manager` endpoint (lines 99-108) with the same pattern but for `"manager"`:

```python
    @app.post("/api/auth/manager")
    async def auth_manager(request: Request):
        body = await request.json()
        password = body.get("password", "")
        stored = db.get_setting("manager_password", "")
        if stored == "":
            resp = JSONResponse({"status": "ok"})
            set_auth_cookie(resp, serializer, "manager")
            return resp
        if is_bcrypt_hash(stored):
            if verify_password(password, stored):
                resp = JSONResponse({"status": "ok"})
                set_auth_cookie(resp, serializer, "manager")
                return resp
        else:
            if password == stored:
                db.set_setting("manager_password", hash_password(stored))
                resp = JSONResponse({"status": "ok"})
                set_auth_cookie(resp, serializer, "manager")
                return resp
        return JSONResponse({"error": "wrong password"}, status_code=401)
```

In `update_setting` endpoint (lines 262-268), hash password values before storing:

```python
    @app.post("/api/settings")
    async def update_setting(request: Request):
        if not check_manager_auth(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        key = body["key"]
        value = body["value"]
        if key in ("picker_password", "manager_password") and value:
            value = hash_password(value)
        db.set_setting(key, value)
        return {"status": "updated"}
```

Also update `require_password_set` in `auth.py` to handle bcrypt hashes properly — currently it just checks `pwd != ""`, which is fine. No change needed there.

**Step 4: Run tests to verify they pass**

Run: `cd /home/cruz/alliance-fulfillment && /home/cruz/.local/bin/uv run pytest tests/test_api.py -v`
Expected: ALL tests PASS (including existing auth tests — those tests set plain-text passwords and the auto-migration should handle them).

**Step 5: Run full test suite**

Run: `cd /home/cruz/alliance-fulfillment && /home/cruz/.local/bin/uv run pytest -v`
Expected: ALL tests PASS.

**Step 6: Commit**

```bash
cd /home/cruz/alliance-fulfillment
git add src/fulfillment/api.py tests/test_api.py
git commit -m "feat(security): wire bcrypt into auth endpoints with plain-text migration"
```

---

### Task 3: Remove Passwords from Settings API Response

**Files:**
- Modify: `src/fulfillment/api.py:251-260` (get_settings endpoint)
- Modify: `src/fulfillment/templates/manager.html:171-207` (settings area)
- Modify: `tests/test_api.py`

**Context:** `GET /api/settings` currently returns raw password values. After Task 2, these are bcrypt hashes — still shouldn't be exposed. Change to boolean flags. Manager dashboard should show "Change Password" fields instead of displaying current values.

**Step 1: Write failing test**

Add to `tests/test_api.py`:

```python
def test_settings_api_does_not_expose_passwords(db):
    """GET /api/settings returns password_set booleans, not actual passwords."""
    from fulfillment.auth import hash_password
    db.set_setting("picker_password", hash_password("secret"))
    app = create_app(db)
    c = TestClient(app)
    resp = c.get("/api/settings")
    data = resp.json()
    assert "picker_password" not in data
    assert "manager_password" not in data
    assert data["picker_password_set"] is True
    assert data["manager_password_set"] is False
```

**Step 2: Run test to verify it fails**

Run: `cd /home/cruz/alliance-fulfillment && /home/cruz/.local/bin/uv run pytest tests/test_api.py::test_settings_api_does_not_expose_passwords -v`
Expected: FAIL — response still contains `picker_password` key.

**Step 3: Implement API change**

In `src/fulfillment/api.py`, replace the `get_settings` endpoint (lines 251-260):

```python
    @app.get("/api/settings")
    async def get_settings():
        return {
            "batch_size": db.get_setting("batch_size", str(config.default_batch_size)),
            "active_picker_slots": db.get_setting("active_picker_slots", str(config.default_picker_slots)),
            "sms_number": db.get_setting("sms_number", ""),
            "refresh_interval": db.get_setting("refresh_interval", str(config.queue_refresh_seconds)),
            "picker_password_set": db.get_setting("picker_password", "") != "",
            "manager_password_set": db.get_setting("manager_password", "") != "",
        }
```

**Step 4: Update manager.html**

In `src/fulfillment/templates/manager.html`, replace the password section in `refreshSettings()` (lines 195-206). The password inputs should show "Change Password" placeholder with empty value, and only send if non-empty:

Replace lines 195-206 with:

```javascript
                <hr style="margin: 1rem 0; border: none; border-top: 1px solid #eee;">
                <div class="setting-row">
                    <label>Picker password:</label>
                    <input type="password" id="set-picker-pwd" value="" placeholder="${s.picker_password_set ? '(password set)' : 'Not set'}">
                    <button onclick="savePassword('picker_password', document.getElementById('set-picker-pwd').value)">Save</button>
                </div>
                <div class="setting-row">
                    <label>Manager password:</label>
                    <input type="password" id="set-manager-pwd" value="" placeholder="${s.manager_password_set ? '(password set)' : 'Not set'}">
                    <button onclick="savePassword('manager_password', document.getElementById('set-manager-pwd').value)">Save</button>
                </div>
```

Add a `savePassword` function in the `<script>` section (after `saveSetting`):

```javascript
        async function savePassword(key, value) {
            if (!value.trim()) return; // Don't save empty passwords
            await fetch('/api/settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({key, value})
            });
            refreshSettings();
        }
```

**Step 5: Run tests**

Run: `cd /home/cruz/alliance-fulfillment && /home/cruz/.local/bin/uv run pytest tests/test_api.py -v`
Expected: ALL PASS. Note: `test_get_and_set_settings` tests `batch_size`, not passwords, so it still passes.

**Step 6: Commit**

```bash
cd /home/cruz/alliance-fulfillment
git add src/fulfillment/api.py src/fulfillment/templates/manager.html tests/test_api.py
git commit -m "feat(security): remove passwords from settings API, show change-password UI"
```

---

### Task 4: Auto-Generate Secret Key

**Files:**
- Modify: `src/fulfillment/api.py:56` (serializer creation)
- Modify: `src/fulfillment/config.py:21`
- Modify: `tests/test_api.py`

**Context:** `APP_SECRET_KEY` defaults to `"alliance-fulfillment-secret-change-me"`. If unchanged, auto-generate a cryptographically random key and persist it to the `settings` table. This runs once on app startup.

**Step 1: Write failing test**

Add to `tests/test_api.py`:

```python
def test_secret_key_auto_generated_when_default(db):
    """When APP_SECRET_KEY is the default placeholder, a random key is generated and stored."""
    import os
    os.environ["APP_SECRET_KEY"] = "alliance-fulfillment-secret-change-me"
    from fulfillment.config import Config
    cfg = Config()
    app = create_app(db)
    stored = db.get_setting("app_secret_key", "")
    assert stored != ""
    assert stored != "alliance-fulfillment-secret-change-me"
    assert len(stored) >= 32
```

**Step 2: Run test to verify it fails**

Run: `cd /home/cruz/alliance-fulfillment && /home/cruz/.local/bin/uv run pytest tests/test_api.py::test_secret_key_auto_generated_when_default -v`
Expected: FAIL — no secret key stored in DB.

**Step 3: Implement**

In `src/fulfillment/api.py`, after `db = db or FulfillmentDB(config.db_path)` (line 48) and before `serializer = make_serializer(config.app_secret_key)` (line 56), add:

```python
    # Auto-generate secret key if using the default placeholder
    secret_key = config.app_secret_key
    if secret_key == "alliance-fulfillment-secret-change-me":
        stored_key = db.get_setting("app_secret_key", "")
        if stored_key:
            secret_key = stored_key
        else:
            import secrets
            secret_key = secrets.token_hex(32)
            db.set_setting("app_secret_key", secret_key)

    serializer = make_serializer(secret_key)
```

And remove the old line `serializer = make_serializer(config.app_secret_key)`.

**Step 4: Run tests**

Run: `cd /home/cruz/alliance-fulfillment && /home/cruz/.local/bin/uv run pytest tests/test_api.py -v`
Expected: ALL PASS.

**Step 5: Commit**

```bash
cd /home/cruz/alliance-fulfillment
git add src/fulfillment/api.py tests/test_api.py
git commit -m "feat(security): auto-generate secret key when using default placeholder"
```

---

### Task 5: Rate Limiting on Auth Endpoints

**Files:**
- Modify: `src/fulfillment/api.py`
- Modify: `tests/test_api.py`

**Context:** Add an in-memory rate limiter — max 5 failed login attempts per IP per minute. No new dependency. Just a dict of `{ip: [timestamps]}`.

**Step 1: Write failing test**

Add to `tests/test_api.py`:

```python
def test_auth_rate_limited_after_5_failures(db):
    """After 5 failed login attempts, the 6th returns 429."""
    db.set_setting("picker_password", "secret123")
    # Need fresh app for clean rate limiter state
    from fulfillment.auth import hash_password
    db.set_setting("picker_password", hash_password("secret123"))
    app = create_app(db)
    c = TestClient(app)
    for i in range(5):
        resp = c.post("/api/auth/picker", json={"password": "wrong"})
        assert resp.status_code == 401, f"Attempt {i+1} should be 401"
    # 6th attempt should be rate limited
    resp = c.post("/api/auth/picker", json={"password": "wrong"})
    assert resp.status_code == 429


def test_auth_rate_limit_does_not_block_correct_password(db):
    """Correct password still works within the rate limit window."""
    from fulfillment.auth import hash_password
    db.set_setting("picker_password", hash_password("secret123"))
    app = create_app(db)
    c = TestClient(app)
    # 3 failed attempts
    for _ in range(3):
        c.post("/api/auth/picker", json={"password": "wrong"})
    # Correct password should still work
    resp = c.post("/api/auth/picker", json={"password": "secret123"})
    assert resp.status_code == 200
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/cruz/alliance-fulfillment && /home/cruz/.local/bin/uv run pytest tests/test_api.py::test_auth_rate_limited_after_5_failures tests/test_api.py::test_auth_rate_limit_does_not_block_correct_password -v`
Expected: FAIL — no rate limiting exists.

**Step 3: Implement**

Add a simple rate limiter inside `create_app()` in `src/fulfillment/api.py`, right after the serializer setup:

```python
    # Rate limiter for auth endpoints
    import time as _time
    _auth_attempts: dict[str, list[float]] = {}
    _RATE_LIMIT_MAX = 5
    _RATE_LIMIT_WINDOW = 60.0  # seconds

    def _check_rate_limit(ip: str) -> bool:
        """Returns True if the request should be allowed, False if rate limited."""
        now = _time.time()
        attempts = _auth_attempts.get(ip, [])
        # Remove attempts outside the window
        attempts = [t for t in attempts if now - t < _RATE_LIMIT_WINDOW]
        _auth_attempts[ip] = attempts
        return len(attempts) < _RATE_LIMIT_MAX

    def _record_failed_attempt(ip: str):
        now = _time.time()
        if ip not in _auth_attempts:
            _auth_attempts[ip] = []
        _auth_attempts[ip].append(now)
```

Then in both `auth_picker` and `auth_manager` endpoints, add rate limit check at the top (after `body = await request.json()`):

```python
        ip = request.client.host if request.client else "unknown"
        if not _check_rate_limit(ip):
            return JSONResponse({"error": "too many attempts, try again later"}, status_code=429)
```

And at the bottom, before the `return JSONResponse({"error": "wrong password"}, status_code=401)` line:

```python
        _record_failed_attempt(ip)
```

**Step 4: Run tests**

Run: `cd /home/cruz/alliance-fulfillment && /home/cruz/.local/bin/uv run pytest tests/test_api.py -v`
Expected: ALL PASS.

**Step 5: Commit**

```bash
cd /home/cruz/alliance-fulfillment
git add src/fulfillment/api.py tests/test_api.py
git commit -m "feat(security): add rate limiting on auth endpoints (5 attempts/min)"
```

---

### Task 6: Atomic Batch Assignment + Release Abandoned Batches

**Files:**
- Modify: `src/fulfillment/db.py:177-201` (assign_batch)
- Modify: `src/fulfillment/db.py` (add release_picker_orders method)
- Modify: `src/fulfillment/api.py` (add release endpoint)
- Modify: `src/fulfillment/templates/manager.html` (add Release button)
- Modify: `tests/test_db.py`
- Modify: `tests/test_api.py`

**Context:** Two fixes in one task because they're closely related. The atomic batch fixes the race condition. The release mechanism fixes the abandoned batch problem.

**Step 1: Write failing tests for atomic batch**

Add to `tests/test_db.py`:

```python
def test_assign_batch_is_atomic(db):
    """Batch assignment uses a single atomic query — assigned orders can't be double-assigned."""
    for i in range(4):
        db.upsert_order(QueuedOrder(
            shipstation_order_id=200 + i, order_number=f"200{i}",
            order_date=datetime(2026, 3, 7, tzinfo=timezone.utc),
            age_hours=72.0, age_bracket=AgeBracket.RED,
            priority_score=1000 - i, zone=OrderZone.GALLON,
            customer_name="Test", ship_to_state="TX", order_value=10.0,
        ))
    picker1 = db.create_picker("Alice")
    picker2 = db.create_picker("Bob")
    batch1 = db.assign_batch(picker1, batch_size=2)
    batch2 = db.assign_batch(picker2, batch_size=2)
    # No overlap — each picker gets different orders
    ids1 = {o.id for o in batch1}
    ids2 = {o.id for o in batch2}
    assert len(ids1 & ids2) == 0
    assert len(batch1) == 2
    assert len(batch2) == 2
```

**Step 2: Write failing tests for release**

Add to `tests/test_db.py`:

```python
def test_release_picker_orders(db):
    """Releasing a picker's orders returns them to queued status."""
    for i in range(3):
        db.upsert_order(QueuedOrder(
            shipstation_order_id=300 + i, order_number=f"300{i}",
            order_date=datetime(2026, 3, 7, tzinfo=timezone.utc),
            age_hours=72.0, age_bracket=AgeBracket.RED,
            priority_score=1000, zone=OrderZone.GALLON,
            customer_name="Test", ship_to_state="TX", order_value=10.0,
        ))
    picker_id = db.create_picker("Maria")
    db.assign_batch(picker_id, batch_size=3)
    # All 3 should be assigned
    assert len(db.get_assigned_orders(picker_id)) == 3
    assert len(db.get_queued_orders()) == 0
    # Release
    db.release_picker_orders(picker_id)
    assert len(db.get_assigned_orders(picker_id)) == 0
    assert len(db.get_queued_orders()) == 3
```

Add to `tests/test_api.py`:

```python
def test_release_picker_orders_api(seeded_client, seeded_db):
    """Manager can release a picker's assigned orders via API."""
    seeded_client.post("/api/pickers/1/batch")
    # Verify orders are assigned
    resp = seeded_client.get("/api/pickers/1/orders")
    assert len(resp.json()) > 0
    # Release
    resp = seeded_client.post("/api/pickers/1/release")
    assert resp.status_code == 200
    # Verify orders are back in queue
    resp = seeded_client.get("/api/pickers/1/orders")
    assert len(resp.json()) == 0
```

**Step 3: Run tests to verify they fail**

Run: `cd /home/cruz/alliance-fulfillment && /home/cruz/.local/bin/uv run pytest tests/test_db.py::test_release_picker_orders tests/test_api.py::test_release_picker_orders_api -v`
Expected: FAIL — `release_picker_orders` doesn't exist.

**Step 4: Implement atomic batch assignment**

Replace `assign_batch` in `src/fulfillment/db.py` (lines 177-201):

```python
    def assign_batch(self, picker_id: int, batch_size: int = 8) -> list[QueuedOrder]:
        with self._conn() as conn:
            now = datetime.now(timezone.utc).isoformat()
            # Atomic: SELECT + UPDATE in one statement — prevents race condition
            conn.execute(
                """UPDATE queued_orders
                   SET status='assigned', assigned_to_picker=?, assigned_at=?
                   WHERE id IN (
                       SELECT id FROM queued_orders
                       WHERE status = 'queued'
                       ORDER BY priority_score DESC
                       LIMIT ?
                   )""",
                (picker_id, now, batch_size)
            )
            # Now fetch what was just assigned
            rows = conn.execute(
                "SELECT * FROM queued_orders WHERE status='assigned' AND assigned_to_picker=? AND assigned_at=? ORDER BY priority_score DESC",
                (picker_id, now)
            ).fetchall()
            if not rows:
                return []
            orders = [self._row_to_order(r) for r in rows]
            order_ids = [o.id for o in orders]
            cursor = conn.execute(
                "INSERT INTO batches (picker_id, order_ids_json) VALUES (?, ?)",
                (picker_id, json.dumps(order_ids))
            )
            batch_id = cursor.lastrowid
            conn.execute(
                "UPDATE pickers SET status='active', current_batch_id=? WHERE id=?",
                (batch_id, picker_id)
            )
            return orders
```

**Step 5: Implement release_picker_orders**

Add to `src/fulfillment/db.py` (after `delete_picker`):

```python
    def release_picker_orders(self, picker_id: int):
        """Release all assigned orders for a picker back to queued status."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE queued_orders SET status='queued', assigned_to_picker=NULL, assigned_at=NULL WHERE status='assigned' AND assigned_to_picker=?",
                (picker_id,)
            )
            conn.execute(
                "UPDATE pickers SET status='idle', current_batch_id=NULL WHERE id=?",
                (picker_id,)
            )
```

**Step 6: Add API endpoint**

Add to `src/fulfillment/api.py` (after the `delete_picker` endpoint):

```python
    @app.post("/api/pickers/{picker_id}/release")
    async def release_picker_orders(picker_id: int, request: Request):
        if not check_manager_auth(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        db.release_picker_orders(picker_id)
        return {"status": "released"}
```

**Step 7: Add Release button to manager.html**

In `src/fulfillment/templates/manager.html`, in `refreshPickers()`, update the table row template (line 142-149). Add a Release button next to Remove:

Replace:
```javascript
                    <td><button class="btn-remove" onclick="removePicker(${p.id}, '${p.name}')">Remove</button></td>
```

With:
```javascript
                    <td>
                        <button class="btn-remove" onclick="releasePicker(${p.id})" style="background:#3498db;margin-right:4px;">Release</button>
                        <button class="btn-remove" onclick="removePicker(${p.id}, '${p.name}')">Remove</button>
                    </td>
```

Add the `releasePicker` function in the `<script>` section (after `removePicker`):

```javascript
        async function releasePicker(pickerId) {
            if (!confirm('Release all assigned orders for this picker back to the queue?')) return;
            await fetch(`/api/pickers/${pickerId}/release`, { method: 'POST' });
            refreshPickers();
            refreshStats();
        }
```

**Step 8: Run tests**

Run: `cd /home/cruz/alliance-fulfillment && /home/cruz/.local/bin/uv run pytest -v`
Expected: ALL PASS.

**Step 9: Commit**

```bash
cd /home/cruz/alliance-fulfillment
git add src/fulfillment/db.py src/fulfillment/api.py src/fulfillment/templates/manager.html tests/test_db.py tests/test_api.py
git commit -m "feat(security): atomic batch assignment + release abandoned batches"
```

---

### Task 7: XSS Prevention in Templates

**Files:**
- Modify: `src/fulfillment/templates/picker.html`
- Modify: `src/fulfillment/templates/manager.html`

**Context:** Both templates use `innerHTML` with data from ShipStation (product names, customer names, SKUs, problem reasons). These are user-controlled and could contain malicious HTML/JS. Add an escape function and use it everywhere user data is rendered.

**Step 1: No unit test needed** — this is a template-only change. We'll verify manually that the escape function works by checking the HTML source.

**Step 2: Add escape function to picker.html**

At the top of the `<script>` section in `src/fulfillment/templates/picker.html` (line 88, after `<script>`), add:

```javascript
        function esc(str) {
            if (str == null) return '';
            return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#039;');
        }
```

Then update `renderBatch` to escape all user data. In the order card template (lines 152-166), wrap every data insertion with `esc()`:

```javascript
        function renderBatch(orders) {
            const container = document.getElementById('batch-container');
            if (orders.length === 0) {
                container.innerHTML = '<div class="status-msg">No orders in queue right now. Check back soon.</div>';
                return;
            }
            let html = `<div class="batch-header"><strong>Your Batch (${orders.length} orders)</strong></div>`;
            let lastZone = '';
            for (const order of orders) {
                if (order.zone !== lastZone && lastZone !== '') {
                    html += `<div class="zone-divider">Zone: ${esc(lastZone)} \u2192 ${esc(order.zone)}</div>`;
                }
                lastZone = order.zone;
                const items = order.line_items.map(li => `${li.quantity}x ${esc(li.name)}`).join(', ');
                html += `
                    <div class="order-card ${esc(order.age_bracket)}">
                        <div class="order-header">
                            <span>#${esc(order.order_number)}</span>
                            <span class="order-age">${Math.round(order.age_hours)}h old</span>
                        </div>
                        <div class="order-items">${items}</div>
                        <div class="order-customer">${esc(order.customer_name)} \u2014 ${esc(order.ship_to_state)}</div>
                        <div class="actions">
                            <button class="btn-print" onclick="printSlip(${order.id})">Print Slip</button>
                            <button class="btn-complete" onclick="completeOrder(${order.id})">Complete</button>
                            <button class="btn-problem" onclick="problemOrder(${order.id})">Problem</button>
                        </div>
                    </div>`;
            }
            container.innerHTML = html;
        }
```

**Step 3: Add escape function to manager.html**

At the top of the `<script>` section in `src/fulfillment/templates/manager.html` (line 106, after `<script>`), add the same `esc()` function.

Then update all `innerHTML` assignments that render user data:

In `refreshPickers()` (lines 141-149), escape picker names:
```javascript
                    <td>${esc(p.name)}</td>
```

In `refreshProblems()` (lines 216-221), escape order number, customer name, and reason:
```javascript
                <div class="problem-item">
                    <strong>#${esc(p.order_number)}</strong> \u2014 ${esc(p.customer_name)}
                    <div class="reason">${esc(p.problem_reason)}</div>
                </div>
```

In `refreshAlerts()` (lines 231-236), escape picker name and product name:
```javascript
                <div class="alert-item">
                    <span class="alert-time">${esc(a.flagged_at)}</span> \u2014
                    <strong>${esc(a.picker_name)}</strong> \u2014 ${esc(a.product_name)}
                    ${a.sms_sent ? '(SMS sent)' : ''}
                </div>
```

In `removePicker()` (line 166), the `pickerName` is used in a `confirm()` dialog which is safe (not HTML), but the inline onclick at line 147 uses string interpolation for the name. Fix to escape it:
```javascript
                    <td><button class="btn-remove" onclick="removePicker(${p.id}, '${esc(p.name).replace(/'/g, "\\'")}')">Remove</button></td>
```

**Step 4: Run full test suite to make sure nothing is broken**

Run: `cd /home/cruz/alliance-fulfillment && /home/cruz/.local/bin/uv run pytest -v`
Expected: ALL PASS.

**Step 5: Commit**

```bash
cd /home/cruz/alliance-fulfillment
git add src/fulfillment/templates/picker.html src/fulfillment/templates/manager.html
git commit -m "feat(security): add XSS escaping to all user data in templates"
```

---

## Final Step: Run Full Test Suite

After all 7 tasks:

```bash
cd /home/cruz/alliance-fulfillment && /home/cruz/.local/bin/uv run pytest -v
```

Expected: ALL tests pass. The system is now hardened for internet-exposed deployment.
