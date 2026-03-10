import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from fulfillment.api import create_app
from fulfillment.db import FulfillmentDB
from fulfillment.models import QueuedOrder, AgeBracket, OrderZone, LineItem


@pytest.fixture
def db(tmp_path):
    return FulfillmentDB(str(tmp_path / "test.db"))


@pytest.fixture
def client(db):
    app = create_app(db)
    return TestClient(app)


@pytest.fixture
def seeded_db(db):
    for i in range(3):
        db.upsert_order(QueuedOrder(
            shipstation_order_id=100 + i,
            order_number=f"100{i}",
            order_date=datetime(2026, 3, 7, tzinfo=timezone.utc),
            age_hours=72.0 - i * 24,
            age_bracket=[AgeBracket.RED, AgeBracket.YELLOW, AgeBracket.GREEN][i],
            priority_score=1000 - i * 100,
            zone=OrderZone.GALLON,
            line_items=[LineItem(sku=f"SKU{i}", name=f"Product {i}", quantity=1)],
            customer_name=f"Customer {i}",
            ship_to_state="TX",
            order_value=10.0 + i * 5,
        ))
    db.create_picker("Maria")
    return db


@pytest.fixture
def seeded_client(seeded_db):
    app = create_app(seeded_db)
    return TestClient(app)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_get_queue_stats(seeded_client):
    resp = seeded_client.get("/api/queue/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3


def test_register_picker(client):
    resp = client.post("/api/pickers", json={"name": "Maria"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Maria"


def test_request_batch(seeded_client):
    resp = seeded_client.post("/api/pickers/1/batch")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["orders"]) == 3


def test_complete_order(seeded_client):
    seeded_client.post("/api/pickers/1/batch")
    resp = seeded_client.post("/api/orders/1/complete", json={"picker_id": 1})
    assert resp.status_code == 200
    stats = seeded_client.get("/api/queue/stats").json()
    assert stats["completed_today"] >= 1


def test_flag_problem(seeded_client):
    seeded_client.post("/api/pickers/1/batch")
    resp = seeded_client.post("/api/orders/1/problem", json={"picker_id": 1, "reason": "Out of stock"})
    assert resp.status_code == 200
    problems = seeded_client.get("/api/queue/problems").json()
    assert len(problems) == 1


def test_create_stock_alert(seeded_client):
    resp = seeded_client.post("/api/alerts/stock", json={
        "picker_id": 1,
        "product_name": "Isopropyl Alcohol 1 Gal",
        "product_sku": "IPA-1GAL",
    })
    assert resp.status_code == 200
    alerts = seeded_client.get("/api/alerts/stock/today").json()
    assert len(alerts) == 1


def test_get_and_set_settings(client):
    resp = client.post("/api/settings", json={"key": "batch_size", "value": "10"})
    assert resp.status_code == 200
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["batch_size"] == "10"


def test_get_pickers(seeded_client):
    resp = seeded_client.get("/api/pickers")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["name"] == "Maria"


# --- New tests for auth and picker management ---


def test_open_access_when_no_password_set(client):
    """When no passwords are configured, dashboards and APIs are accessible."""
    # Manager dashboard should be accessible (no redirect)
    resp = client.get("/manager", follow_redirects=False)
    assert resp.status_code == 200

    # Picker dashboard should be accessible
    resp = client.get("/picker", follow_redirects=False)
    assert resp.status_code == 200

    # Settings POST should work
    resp = client.post("/api/settings", json={"key": "batch_size", "value": "5"})
    assert resp.status_code == 200

    # Picker creation should work
    resp = client.post("/api/pickers", json={"name": "Test"})
    assert resp.status_code == 200


def test_picker_login_required_when_password_set(db):
    """When picker password is set, picker routes require auth."""
    db.set_setting("picker_password", "secret123")
    app = create_app(db)
    c = TestClient(app)

    # Picker dashboard redirects to login
    resp = c.get("/picker", follow_redirects=False)
    assert resp.status_code == 302
    assert "/picker/login" in resp.headers["location"]

    # Picker API routes return 401
    db.create_picker("Test")
    resp = c.post("/api/pickers/1/batch")
    assert resp.status_code == 401

    # Login with wrong password fails
    resp = c.post("/api/auth/picker", json={"password": "wrong"})
    assert resp.status_code == 401

    # Login with correct password succeeds and sets cookie
    resp = c.post("/api/auth/picker", json={"password": "secret123"})
    assert resp.status_code == 200

    # After login, picker dashboard is accessible (cookie set)
    resp = c.get("/picker", follow_redirects=False)
    assert resp.status_code == 200


def test_manager_login_required_when_password_set(db):
    """When manager password is set, manager routes require auth."""
    db.set_setting("manager_password", "mgr456")
    app = create_app(db)
    c = TestClient(app)

    # Manager dashboard redirects to login
    resp = c.get("/manager", follow_redirects=False)
    assert resp.status_code == 302
    assert "/manager/login" in resp.headers["location"]

    # Settings POST returns 401
    resp = c.post("/api/settings", json={"key": "batch_size", "value": "5"})
    assert resp.status_code == 401

    # Login with correct password
    resp = c.post("/api/auth/manager", json={"password": "mgr456"})
    assert resp.status_code == 200

    # After login, manager dashboard is accessible
    resp = c.get("/manager", follow_redirects=False)
    assert resp.status_code == 200

    # Settings POST works after login
    resp = c.post("/api/settings", json={"key": "batch_size", "value": "5"})
    assert resp.status_code == 200


def test_delete_picker(db):
    """Manager can delete a picker."""
    picker_id = db.create_picker("ToRemove")
    app = create_app(db)
    c = TestClient(app)

    # Verify picker exists
    resp = c.get("/api/pickers")
    assert any(p["name"] == "ToRemove" for p in resp.json())

    # Delete picker
    resp = c.delete(f"/api/pickers/{picker_id}")
    assert resp.status_code == 200

    # Verify picker is gone
    resp = c.get("/api/pickers")
    assert not any(p["name"] == "ToRemove" for p in resp.json())


def test_picker_dropdown_list(db):
    """Picker list endpoint returns registered pickers for dropdown."""
    db.create_picker("Alice")
    db.create_picker("Bob")
    app = create_app(db)
    c = TestClient(app)

    resp = c.get("/api/pickers")
    assert resp.status_code == 200
    names = [p["name"] for p in resp.json()]
    assert "Alice" in names
    assert "Bob" in names


def test_delete_picker_requires_manager_auth(db):
    """Delete picker returns 401 when manager password is set and no auth."""
    db.set_setting("manager_password", "mgr456")
    picker_id = db.create_picker("Worker")
    app = create_app(db)
    c = TestClient(app)

    resp = c.delete(f"/api/pickers/{picker_id}")
    assert resp.status_code == 401


def test_logout_clears_cookies(client):
    """Logout endpoint clears auth cookies and redirects."""
    resp = client.get("/api/auth/logout", follow_redirects=False)
    assert resp.status_code == 302
    assert "/picker/login" in resp.headers["location"]


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
