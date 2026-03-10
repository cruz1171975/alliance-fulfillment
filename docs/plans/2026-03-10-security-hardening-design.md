# Security & Reliability Hardening — Phase 1

**Goal:** Fix critical security and operational issues before internet-exposed production deployment.

**Scope:** 7 changes. Security fixes + one race condition fix + one operational tool. No new features.

**Timeline:** This week.

---

## 1. Password Hashing (bcrypt)

Passwords are currently stored as plain text in the `settings` table and compared with `==`.

**Change:** Use `passlib[bcrypt]` to hash passwords on storage and verify with constant-time comparison. On first startup after upgrade, detect unhashed passwords and hash them in place.

**Files:** `api.py`, `auth.py`, `pyproject.toml`

---

## 2. Remove Passwords from Settings API

`GET /api/settings` returns raw passwords. The manager dashboard displays them in input fields.

**Change:** Return `picker_password_set: true/false` and `manager_password_set: true/false` instead. Manager dashboard shows a "Change Password" field (empty = no change) instead of the current value.

**Files:** `api.py`, `templates/manager.html`

---

## 3. Auto-Generate Secret Key

`APP_SECRET_KEY` defaults to `"alliance-fulfillment-secret-change-me"`. Anyone who knows this can forge auth cookies.

**Change:** On startup, if the key is still the default, generate a cryptographically random 32-byte key and persist it to the `settings` table. Subsequent startups read from DB.

**Files:** `api.py` or `config.py`

---

## 4. Rate Limiting on Auth Endpoints

No rate limiting exists. Passwords can be brute-forced at unlimited speed.

**Change:** In-memory rate limiter — max 5 failed attempts per IP per minute on `/api/auth/picker` and `/api/auth/manager`. Returns 429 after limit. Dict of `{ip: [timestamps]}`, cleaned periodically. No new dependency.

**Files:** `api.py`

---

## 5. Atomic Batch Assignment

Batch assignment uses separate SELECT then UPDATE. Two concurrent pickers can receive the same orders.

**Change:** Single atomic SQL:
```sql
UPDATE queued_orders
SET status='assigned', assigned_to_picker=?, assigned_at=?
WHERE id IN (
  SELECT id FROM queued_orders
  WHERE status = 'queued'
  ORDER BY priority_score DESC
  LIMIT ?
)
```

**Files:** `db.py`

---

## 6. Release Abandoned Batches

If a picker disappears, their assigned orders are stuck forever. No recovery mechanism.

**Change:** Add a "Release Orders" button per picker in the manager dashboard. Sets all that picker's assigned orders back to `status='queued'`, clears `assigned_to_picker`. No automatic timeout — manager makes the call.

**Files:** `db.py`, `api.py`, `templates/manager.html`

---

## 7. XSS Prevention

Both dashboards use `innerHTML` with unsanitized data from ShipStation (customer-entered product names, addresses). Malicious input executes in picker browsers.

**Change:** Add a text-escape helper function in templates that converts `<`, `>`, `&`, `"`, `'` to HTML entities. Use it everywhere data is rendered into the DOM.

**Files:** `templates/picker.html`, `templates/manager.html`
