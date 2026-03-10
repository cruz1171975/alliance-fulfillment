import sqlite3
import json
from datetime import datetime, timezone, date
from fulfillment.models import (
    QueuedOrder, Picker, StockAlert, AgeBracket, OrderZone, LineItem,
    QueueSettings,
)


class FulfillmentDB:
    def __init__(self, db_path: str = "fulfillment.db"):
        self.db_path = db_path
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS queued_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shipstation_order_id INTEGER UNIQUE NOT NULL,
                    order_number TEXT NOT NULL,
                    order_date TEXT NOT NULL,
                    age_hours REAL DEFAULT 0,
                    age_bracket TEXT DEFAULT 'green',
                    priority_score REAL DEFAULT 0,
                    zone TEXT DEFAULT 'other',
                    line_items_json TEXT DEFAULT '[]',
                    customer_name TEXT DEFAULT '',
                    ship_to_state TEXT DEFAULT '',
                    order_value REAL DEFAULT 0,
                    assigned_to_picker INTEGER,
                    assigned_at TEXT,
                    status TEXT DEFAULT 'queued',
                    problem_reason TEXT,
                    has_priority_tag INTEGER DEFAULT 0,
                    tag_ids_json TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS pickers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    status TEXT DEFAULT 'idle',
                    current_batch_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    picker_id INTEGER NOT NULL,
                    order_ids_json TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    completed_at TEXT,
                    status TEXT DEFAULT 'active',
                    FOREIGN KEY (picker_id) REFERENCES pickers(id)
                );

                CREATE TABLE IF NOT EXISTS completions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL,
                    picker_id INTEGER NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS stock_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    picker_id INTEGER NOT NULL,
                    product_name TEXT NOT NULL,
                    product_sku TEXT DEFAULT '',
                    restock_qty INTEGER DEFAULT 0,
                    order_id INTEGER,
                    flagged_at TEXT NOT NULL DEFAULT (datetime('now')),
                    sms_sent INTEGER DEFAULT 0,
                    FOREIGN KEY (picker_id) REFERENCES pickers(id)
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)

            # Migrations for existing databases
            cursor = conn.execute("PRAGMA table_info(stock_alerts)")
            columns = [row[1] for row in cursor.fetchall()]
            if "restock_qty" not in columns:
                conn.execute("ALTER TABLE stock_alerts ADD COLUMN restock_qty INTEGER DEFAULT 0")
            if "order_id" not in columns:
                conn.execute("ALTER TABLE stock_alerts ADD COLUMN order_id INTEGER")

    def upsert_order(self, order: QueuedOrder):
        line_items_json = json.dumps([li.model_dump() for li in order.line_items])
        tag_ids_json = json.dumps(order.tag_ids)
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO queued_orders (
                    shipstation_order_id, order_number, order_date,
                    age_hours, age_bracket, priority_score, zone,
                    line_items_json, customer_name, ship_to_state,
                    order_value, has_priority_tag, tag_ids_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(shipstation_order_id) DO UPDATE SET
                    age_hours=excluded.age_hours,
                    age_bracket=excluded.age_bracket,
                    priority_score=excluded.priority_score,
                    zone=excluded.zone,
                    line_items_json=excluded.line_items_json,
                    order_value=excluded.order_value,
                    has_priority_tag=excluded.has_priority_tag,
                    tag_ids_json=excluded.tag_ids_json,
                    updated_at=datetime('now')
            """, (
                order.shipstation_order_id, order.order_number,
                order.order_date.isoformat(), order.age_hours,
                order.age_bracket.value, order.priority_score,
                order.zone.value, line_items_json,
                order.customer_name, order.ship_to_state,
                order.order_value, int(order.has_priority_tag), tag_ids_json,
            ))

    def _row_to_order(self, row: sqlite3.Row) -> QueuedOrder:
        return QueuedOrder(
            id=row["id"],
            shipstation_order_id=row["shipstation_order_id"],
            order_number=row["order_number"],
            order_date=datetime.fromisoformat(row["order_date"]),
            age_hours=row["age_hours"],
            age_bracket=AgeBracket(row["age_bracket"]),
            priority_score=row["priority_score"],
            zone=OrderZone(row["zone"]),
            line_items=[LineItem(**li) for li in json.loads(row["line_items_json"])],
            customer_name=row["customer_name"],
            ship_to_state=row["ship_to_state"],
            order_value=row["order_value"],
            assigned_to_picker=row["assigned_to_picker"],
            status=row["status"],
            problem_reason=row["problem_reason"],
            has_priority_tag=bool(row["has_priority_tag"]),
            tag_ids=json.loads(row["tag_ids_json"]),
        )

    def get_order_by_id(self, order_id: int) -> QueuedOrder | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM queued_orders WHERE id = ?", (order_id,)).fetchone()
            return self._row_to_order(row) if row else None

    def get_queued_orders(self) -> list[QueuedOrder]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM queued_orders WHERE status = 'queued' ORDER BY priority_score DESC"
            ).fetchall()
            return [self._row_to_order(r) for r in rows]

    def get_assigned_orders(self, picker_id: int) -> list[QueuedOrder]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM queued_orders WHERE status = 'assigned' AND assigned_to_picker = ? ORDER BY priority_score DESC",
                (picker_id,)
            ).fetchall()
            return [self._row_to_order(r) for r in rows]

    def create_picker(self, name: str) -> int:
        with self._conn() as conn:
            cursor = conn.execute(
                "INSERT INTO pickers (name) VALUES (?) ON CONFLICT(name) DO UPDATE SET name=name RETURNING id",
                (name,)
            )
            return cursor.fetchone()["id"]

    def get_picker(self, picker_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM pickers WHERE id = ?", (picker_id,)).fetchone()
            return dict(row) if row else None

    def get_all_pickers(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM pickers ORDER BY name").fetchall()
            return [dict(r) for r in rows]

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

    def complete_order(self, order_id: int, picker_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE queued_orders SET status='completed', updated_at=datetime('now') WHERE id=?",
                (order_id,)
            )
            conn.execute(
                "INSERT INTO completions (order_id, picker_id) VALUES (?, ?)",
                (order_id, picker_id)
            )

    def flag_problem(self, order_id: int, picker_id: int, reason: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE queued_orders SET status='problem', problem_reason=?, updated_at=datetime('now') WHERE id=?",
                (reason, order_id)
            )

    def get_problem_orders(self) -> list[QueuedOrder]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM queued_orders WHERE status = 'problem' ORDER BY updated_at DESC"
            ).fetchall()
            return [self._row_to_order(r) for r in rows]

    def get_picker_stats(self, picker_id: int) -> dict:
        with self._conn() as conn:
            today = date.today().isoformat()
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM completions WHERE picker_id=? AND completed_at >= ?",
                (picker_id, today)
            ).fetchone()
            return {"orders_completed_today": row["cnt"]}

    def create_stock_alert(self, picker_id: int, product_name: str, product_sku: str = "", restock_qty: int = 0, order_id: int | None = None) -> int:
        with self._conn() as conn:
            cursor = conn.execute(
                "INSERT INTO stock_alerts (picker_id, product_name, product_sku, restock_qty, order_id) VALUES (?, ?, ?, ?, ?)",
                (picker_id, product_name, product_sku, restock_qty, order_id)
            )
            return cursor.lastrowid

    def mark_alert_sent(self, alert_id: int):
        with self._conn() as conn:
            conn.execute("UPDATE stock_alerts SET sms_sent=1 WHERE id=?", (alert_id,))

    def get_stock_alerts_today(self) -> list[dict]:
        with self._conn() as conn:
            today = date.today().isoformat()
            rows = conn.execute(
                """SELECT sa.*, p.name as picker_name
                   FROM stock_alerts sa JOIN pickers p ON sa.picker_id = p.id
                   WHERE sa.flagged_at >= ? ORDER BY sa.flagged_at DESC""",
                (today,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_setting(self, key: str, default: str = "") -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value)
            )

    def get_queue_stats(self) -> dict:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) as cnt FROM queued_orders WHERE status IN ('queued', 'assigned')").fetchone()["cnt"]
            brackets = {}
            for bracket in ["red", "yellow", "orange", "green"]:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM queued_orders WHERE status IN ('queued', 'assigned') AND age_bracket=?",
                    (bracket,)
                ).fetchone()
                brackets[bracket] = row["cnt"]
            today = date.today().isoformat()
            completed = conn.execute(
                "SELECT COUNT(*) as cnt FROM completions WHERE completed_at >= ?", (today,)
            ).fetchone()["cnt"]
            problems = conn.execute(
                "SELECT COUNT(*) as cnt FROM queued_orders WHERE status='problem'"
            ).fetchone()["cnt"]
            oldest = conn.execute(
                "SELECT MAX(age_hours) as max_age FROM queued_orders WHERE status IN ('queued', 'assigned')"
            ).fetchone()["max_age"] or 0
            return {
                "total": total,
                "red": brackets["red"], "yellow": brackets["yellow"],
                "orange": brackets["orange"], "green": brackets["green"],
                "completed_today": completed,
                "problems": problems,
                "oldest_age_hours": oldest,
            }

    def delete_picker(self, picker_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM pickers WHERE id=?", (picker_id,))

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

    def remove_shipped_orders(self, active_shipstation_ids: set[int]):
        """Remove orders from queue that are no longer in ShipStation awaiting_shipment."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, shipstation_order_id FROM queued_orders WHERE status='queued'"
            ).fetchall()
            for row in rows:
                if row["shipstation_order_id"] not in active_shipstation_ids:
                    conn.execute("DELETE FROM queued_orders WHERE id=?", (row["id"],))
