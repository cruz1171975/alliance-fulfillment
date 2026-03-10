import pytest
from datetime import datetime, timezone
from fulfillment.db import FulfillmentDB
from fulfillment.models import QueuedOrder, AgeBracket, OrderZone, LineItem


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test_fulfillment.db")
    return FulfillmentDB(db_path)


def test_upsert_and_get_queued_orders(db):
    order = QueuedOrder(
        shipstation_order_id=100,
        order_number="1001",
        order_date=datetime(2026, 3, 7, 12, 0, tzinfo=timezone.utc),
        age_hours=72.0,
        age_bracket=AgeBracket.RED,
        priority_score=1000,
        zone=OrderZone.GALLON,
        line_items=[LineItem(sku="IPA-1GAL", name="Isopropyl Alcohol", quantity=1)],
        customer_name="John Smith",
        ship_to_state="TX",
        order_value=15.99,
    )
    db.upsert_order(order)
    orders = db.get_queued_orders()
    assert len(orders) == 1
    assert orders[0].order_number == "1001"
    assert orders[0].age_bracket == AgeBracket.RED


def test_upsert_order_updates_existing(db):
    order = QueuedOrder(
        shipstation_order_id=100, order_number="1001",
        order_date=datetime(2026, 3, 7, tzinfo=timezone.utc),
        age_hours=48.0, age_bracket=AgeBracket.YELLOW,
        priority_score=500, zone=OrderZone.GALLON,
        customer_name="John Smith", ship_to_state="TX", order_value=15.99,
    )
    db.upsert_order(order)
    order.age_hours = 72.0
    order.age_bracket = AgeBracket.RED
    order.priority_score = 1000
    db.upsert_order(order)
    orders = db.get_queued_orders()
    assert len(orders) == 1
    assert orders[0].age_bracket == AgeBracket.RED


def test_assign_orders_to_picker(db):
    for i in range(3):
        db.upsert_order(QueuedOrder(
            shipstation_order_id=100 + i, order_number=f"100{i}",
            order_date=datetime(2026, 3, 7, tzinfo=timezone.utc),
            age_hours=72.0, age_bracket=AgeBracket.RED,
            priority_score=1000 - i, zone=OrderZone.GALLON,
            customer_name="Test", ship_to_state="TX", order_value=10.0,
        ))
    picker_id = db.create_picker("Maria")
    assigned = db.assign_batch(picker_id, batch_size=2)
    assert len(assigned) == 2
    remaining = db.get_queued_orders()
    assert len(remaining) == 1


def test_complete_order(db):
    db.upsert_order(QueuedOrder(
        shipstation_order_id=100, order_number="1001",
        order_date=datetime(2026, 3, 7, tzinfo=timezone.utc),
        age_hours=72.0, age_bracket=AgeBracket.RED,
        priority_score=1000, zone=OrderZone.GALLON,
        customer_name="Test", ship_to_state="TX", order_value=10.0,
    ))
    picker_id = db.create_picker("Maria")
    assigned = db.assign_batch(picker_id, batch_size=1)
    db.complete_order(assigned[0].id, picker_id)
    orders = db.get_queued_orders()
    assert len(orders) == 0
    stats = db.get_picker_stats(picker_id)
    assert stats["orders_completed_today"] == 1


def test_flag_problem_order(db):
    db.upsert_order(QueuedOrder(
        shipstation_order_id=100, order_number="1001",
        order_date=datetime(2026, 3, 7, tzinfo=timezone.utc),
        age_hours=72.0, age_bracket=AgeBracket.RED,
        priority_score=1000, zone=OrderZone.GALLON,
        customer_name="Test", ship_to_state="TX", order_value=10.0,
    ))
    picker_id = db.create_picker("Maria")
    assigned = db.assign_batch(picker_id, batch_size=1)
    db.flag_problem(assigned[0].id, picker_id, "Out of stock")
    problems = db.get_problem_orders()
    assert len(problems) == 1
    assert problems[0].problem_reason == "Out of stock"


def test_create_stock_alert(db):
    picker_id = db.create_picker("Maria")
    alert_id = db.create_stock_alert(picker_id, "Isopropyl Alcohol 1 Gal", "IPA-1GAL")
    alerts = db.get_stock_alerts_today()
    assert len(alerts) == 1
    assert alerts[0]["product_name"] == "Isopropyl Alcohol 1 Gal"


def test_create_stock_alert_with_restock_qty(db):
    picker_id = db.create_picker("Maria")
    alert_id = db.create_stock_alert(
        picker_id, "Isopropyl Alcohol 1 Gal", "IPA-1GAL",
        restock_qty=10, order_id=42
    )
    alerts = db.get_stock_alerts_today()
    assert len(alerts) == 1
    assert alerts[0]["product_name"] == "Isopropyl Alcohol 1 Gal"
    assert alerts[0]["restock_qty"] == 10
    assert alerts[0]["order_id"] == 42


def test_get_and_set_settings(db):
    db.set_setting("batch_size", "10")
    assert db.get_setting("batch_size", "8") == "10"
    assert db.get_setting("nonexistent", "default") == "default"


def test_get_queue_stats(db):
    for i, bracket in enumerate([AgeBracket.RED, AgeBracket.YELLOW, AgeBracket.GREEN]):
        db.upsert_order(QueuedOrder(
            shipstation_order_id=100 + i, order_number=f"100{i}",
            order_date=datetime(2026, 3, 7, tzinfo=timezone.utc),
            age_hours=72.0 - i * 24, age_bracket=bracket,
            priority_score=1000 - i * 100, zone=OrderZone.GALLON,
            customer_name="Test", ship_to_state="TX", order_value=10.0,
        ))
    stats = db.get_queue_stats()
    assert stats["total"] == 3
    assert stats["red"] == 1
    assert stats["yellow"] == 1
    assert stats["green"] == 1


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
    assert len(db.get_assigned_orders(picker_id)) == 3
    assert len(db.get_queued_orders()) == 0
    db.release_picker_orders(picker_id)
    assert len(db.get_assigned_orders(picker_id)) == 0
    assert len(db.get_queued_orders()) == 3
