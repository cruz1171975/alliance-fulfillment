from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pathlib import Path
from fulfillment.db import FulfillmentDB
from fulfillment.sms import SMSNotifier
from fulfillment.shipstation import ShipStationAPI
from fulfillment.config import config
from fulfillment.auth import make_serializer, set_auth_cookie, check_auth, require_password_set, hash_password, verify_password, is_bcrypt_hash

LOGIN_HTML = """
<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Login - Alliance Fulfillment</title>
<style>
  body {{ font-family: -apple-system, sans-serif; background: #f5f5f5; display: flex; justify-content: center; align-items: center; height: 100vh; }}
  .login {{ background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); width: 300px; }}
  .login h2 {{ margin: 0 0 1rem; color: #1a1a2e; }}
  .login input {{ width: 100%; padding: 0.75rem; border: 1px solid #ddd; border-radius: 4px; font-size: 1rem; margin-bottom: 1rem; box-sizing: border-box; }}
  .login button {{ width: 100%; padding: 0.75rem; background: #1a1a2e; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 1rem; }}
  .error {{ color: #e74c3c; font-size: 0.9rem; margin-bottom: 0.5rem; display: none; }}
</style></head>
<body><div class="login">
  <h2>{title}</h2>
  <div class="error" id="error">Wrong password</div>
  <input type="password" id="password" placeholder="Enter password" onkeydown="if(event.key==='Enter')login()">
  <button onclick="login()">Sign In</button>
</div>
<script>
async function login() {{
  const pwd = document.getElementById('password').value;
  const resp = await fetch('/api/auth/{role}', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{password: pwd}})
  }});
  if (resp.ok) {{ window.location.href = '/{role}'; }}
  else {{ document.getElementById('error').style.display = 'block'; }}
}}
</script></body></html>
"""


def create_app(db: FulfillmentDB | None = None, sms: SMSNotifier | None = None, ss_api: ShipStationAPI | None = None) -> FastAPI:
    app = FastAPI(title="Alliance Fulfillment Queue")
    db = db or FulfillmentDB(config.db_path)
    ss_api = ss_api or ShipStationAPI(api_key=config.shipstation_api_key, api_secret=config.shipstation_api_secret)
    sms = sms or SMSNotifier(
        account_sid=config.twilio_account_sid,
        auth_token=config.twilio_auth_token,
        from_number=config.twilio_from_number,
    )

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

    templates_dir = Path(__file__).parent / "templates"
    if templates_dir.exists():
        templates = Jinja2Templates(directory=str(templates_dir))
    else:
        templates = None

    # --- Auth helpers ---

    def check_picker_auth(request: Request) -> bool:
        if not require_password_set(db, "picker"):
            return True  # No password set = open access
        return check_auth(request, serializer, "picker")

    def check_manager_auth(request: Request) -> bool:
        if not require_password_set(db, "manager"):
            return True
        return check_auth(request, serializer, "manager")

    # --- Login pages ---

    @app.get("/picker/login", response_class=HTMLResponse)
    async def picker_login_page():
        return HTMLResponse(LOGIN_HTML.format(title="Picker Login", role="picker"))

    @app.get("/manager/login", response_class=HTMLResponse)
    async def manager_login_page():
        return HTMLResponse(LOGIN_HTML.format(title="Manager Login", role="manager"))

    # --- Auth API ---

    @app.post("/api/auth/picker")
    async def auth_picker(request: Request):
        body = await request.json()
        ip = request.client.host if request.client else "unknown"
        if not _check_rate_limit(ip):
            return JSONResponse({"error": "too many attempts, try again later"}, status_code=429)
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
        _record_failed_attempt(ip)
        return JSONResponse({"error": "wrong password"}, status_code=401)

    @app.post("/api/auth/manager")
    async def auth_manager(request: Request):
        body = await request.json()
        ip = request.client.host if request.client else "unknown"
        if not _check_rate_limit(ip):
            return JSONResponse({"error": "too many attempts, try again later"}, status_code=429)
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
        _record_failed_attempt(ip)
        return JSONResponse({"error": "wrong password"}, status_code=401)

    @app.get("/api/auth/logout")
    async def auth_logout():
        resp = RedirectResponse("/picker/login", status_code=302)
        resp.delete_cookie("picker_auth")
        resp.delete_cookie("manager_auth")
        return resp

    # --- Health ---

    @app.get("/health")
    async def health():
        stats = db.get_queue_stats()
        return {"status": "ok", "queue": stats}

    # --- Queue API ---

    @app.get("/api/queue/stats")
    async def queue_stats():
        return db.get_queue_stats()

    @app.get("/api/queue/problems")
    async def queue_problems():
        problems = db.get_problem_orders()
        return [p.model_dump(mode="json") for p in problems]

    # --- Picker API ---

    @app.get("/api/pickers")
    async def list_pickers():
        pickers = db.get_all_pickers()
        for p in pickers:
            stats = db.get_picker_stats(p["id"])
            p.update(stats)
        return pickers

    @app.post("/api/pickers")
    async def register_picker(request: Request):
        if not check_manager_auth(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        name = body["name"]
        picker_id = db.create_picker(name)
        picker = db.get_picker(picker_id)
        return picker

    @app.delete("/api/pickers/{picker_id}")
    async def delete_picker(picker_id: int, request: Request):
        if not check_manager_auth(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        db.delete_picker(picker_id)
        return {"status": "deleted"}

    @app.post("/api/pickers/{picker_id}/release")
    async def release_picker_orders(picker_id: int, request: Request):
        if not check_manager_auth(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        db.release_picker_orders(picker_id)
        return {"status": "released"}

    @app.post("/api/pickers/{picker_id}/batch")
    async def request_batch(picker_id: int, request: Request):
        if not check_picker_auth(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        batch_size = int(db.get_setting("batch_size", str(config.default_batch_size)))
        orders = db.assign_batch(picker_id, batch_size=batch_size)
        return {"orders": [o.model_dump(mode="json") for o in orders]}

    @app.get("/api/pickers/{picker_id}/orders")
    async def picker_orders(picker_id: int):
        orders = db.get_assigned_orders(picker_id)
        return [o.model_dump(mode="json") for o in orders]

    # --- Order Actions ---

    @app.post("/api/orders/{order_id}/complete")
    async def complete_order(order_id: int, request: Request):
        if not check_picker_auth(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        picker_id = body["picker_id"]
        db.complete_order(order_id, picker_id)
        return {"status": "completed"}

    @app.post("/api/orders/{order_id}/problem")
    async def flag_problem(order_id: int, request: Request):
        if not check_picker_auth(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        picker_id = body["picker_id"]
        reason = body["reason"]
        db.flag_problem(order_id, picker_id, reason)
        return {"status": "flagged"}

    # --- Packing Slip ---

    @app.get("/api/orders/{order_id}/packing-slip")
    async def get_packing_slip(order_id: int, request: Request):
        if not check_picker_auth(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        order = db.get_order_by_id(order_id)
        if not order:
            return JSONResponse({"error": "order not found"}, status_code=404)
        # Fetch full order from ShipStation for ship-to address
        ss_order_dict = None
        try:
            ss_order = await ss_api.get_order(order.shipstation_order_id)
            ss_order_dict = ss_order.model_dump()
        except Exception:
            pass  # Fall back to what we have in our DB
        from fulfillment.packing_slip import generate_packing_slip
        pdf_bytes = generate_packing_slip(order, ss_order_dict)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"inline; filename=packing-slip-{order.order_number}.pdf"}
        )

    @app.get("/api/batch/packing-slips")
    async def get_batch_packing_slips(request: Request):
        if not check_picker_auth(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        ids_param = request.query_params.get("ids", "")
        if not ids_param:
            return JSONResponse({"error": "no order ids provided"}, status_code=400)
        order_ids = [int(x) for x in ids_param.split(",") if x.strip()]
        if not order_ids:
            return JSONResponse({"error": "no order ids provided"}, status_code=400)
        slips = []
        for oid in order_ids:
            order = db.get_order_by_id(oid)
            if not order:
                continue
            ss_order_dict = None
            try:
                ss_order = await ss_api.get_order(order.shipstation_order_id)
                ss_order_dict = ss_order.model_dump()
            except Exception:
                pass
            slips.append((order, ss_order_dict))
        if not slips:
            return JSONResponse({"error": "no orders found"}, status_code=404)
        from fulfillment.packing_slip import generate_batch_packing_slips
        pdf_bytes = generate_batch_packing_slips(slips)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": "inline; filename=batch-packing-slips.pdf"}
        )

    # --- Stock Alerts ---

    @app.post("/api/alerts/stock")
    async def create_stock_alert(request: Request):
        if not check_picker_auth(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        picker_id = body["picker_id"]
        product_name = body["product_name"]
        product_sku = body.get("product_sku", "")
        restock_qty = body.get("restock_qty", 0)
        order_id = body.get("order_id")
        order_number = body.get("order_number", "")
        alert_id = db.create_stock_alert(picker_id, product_name, product_sku, restock_qty=restock_qty, order_id=order_id)

        # Send SMS
        sms_number = db.get_setting("sms_number", "")
        if sms_number:
            picker = db.get_picker(picker_id)
            picker_name = picker["name"] if picker else "Unknown"
            now_str = datetime.now(timezone.utc).strftime("%I:%M %p")
            message = sms.format_restock_message(product_name, restock_qty, order_number, picker_name, now_str)
            sent = sms.send_sms(sms_number, message)
            if sent:
                db.mark_alert_sent(alert_id)

        return {"alert_id": alert_id}

    @app.get("/api/alerts/stock/today")
    async def stock_alerts_today():
        return db.get_stock_alerts_today()

    # --- Settings ---

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

    # --- HTML Dashboards ---

    @app.get("/picker", response_class=HTMLResponse)
    async def picker_dashboard(request: Request):
        if not check_picker_auth(request):
            return RedirectResponse("/picker/login", status_code=302)
        if not templates:
            return HTMLResponse("<h1>Templates not found</h1>", status_code=500)
        return templates.TemplateResponse("picker.html", {"request": request})

    @app.get("/manager", response_class=HTMLResponse)
    async def manager_dashboard(request: Request):
        if not check_manager_auth(request):
            return RedirectResponse("/manager/login", status_code=302)
        if not templates:
            return HTMLResponse("<h1>Templates not found</h1>", status_code=500)
        return templates.TemplateResponse("manager.html", {"request": request})

    return app


def main():
    import uvicorn
    import asyncio
    from fulfillment.sync import QueueSync
    from fulfillment.queue import QueueEngine
    from fulfillment.shipstation import ShipStationAPI

    db_instance = FulfillmentDB(config.db_path)
    app_instance = create_app(db_instance)

    async def start_sync(db):
        ss_api = ShipStationAPI(api_key=config.shipstation_api_key, api_secret=config.shipstation_api_secret)
        engine = QueueEngine()
        sync = QueueSync(db=db, ss_api=ss_api, engine=engine)
        interval = int(db.get_setting("refresh_interval", str(config.queue_refresh_seconds)))
        await sync.run_loop(interval_seconds=interval)

    uv_config = uvicorn.Config(app_instance, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(uv_config)

    async def run():
        sync_task = asyncio.create_task(start_sync(db_instance))
        await server.serve()
        sync_task.cancel()

    asyncio.run(run())
