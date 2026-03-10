import asyncio
import base64
import httpx
import time
from collections import OrderedDict
from typing import Any
from pydantic import BaseModel


# --- LRU Cache (inline) ---

class LRUCache:
    def __init__(self, max_size: int = 100, ttl_seconds: float = 300.0):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        if key not in self._cache:
            return None
        ts, value = self._cache[key]
        if time.time() - ts > self.ttl_seconds:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return value

    def set(self, key: str, value: Any):
        if key in self._cache:
            del self._cache[key]
        elif len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)
        self._cache[key] = (time.time(), value)

    def clear(self):
        self._cache.clear()


# --- ShipStation Models ---

class ShipStationAddress(BaseModel):
    name: str | None = None
    company: str | None = None
    street1: str | None = None
    street2: str | None = None
    city: str | None = None
    state: str | None = None
    postalCode: str | None = None
    country: str | None = "US"
    phone: str | None = None
    residential: bool | None = None


class ShipStationItem(BaseModel):
    lineItemKey: str | None = None
    sku: str | None = None
    name: str | None = None
    quantity: int = 1
    unitPrice: float | None = None
    weight: dict | None = None


class ShipStationWeight(BaseModel):
    value: float
    units: str = "pounds"


class ShipStationDimensions(BaseModel):
    length: float
    width: float
    height: float
    units: str = "inches"


class ShipStationOrder(BaseModel):
    orderId: int | None = None
    orderNumber: str
    orderStatus: str | None = None
    orderDate: str | None = None
    customerEmail: str | None = None
    shipTo: ShipStationAddress
    billTo: ShipStationAddress | None = None
    items: list[ShipStationItem] = []
    amountPaid: float | None = None
    shippingAmount: float | None = None
    internalNotes: str | None = None
    tagIds: list[int] | None = None
    carrierCode: str | None = None
    serviceCode: str | None = None
    weight: ShipStationWeight | None = None
    dimensions: ShipStationDimensions | None = None


class ShipStationShipment(BaseModel):
    shipmentId: int
    orderId: int
    orderNumber: str
    trackingNumber: str | None = None
    carrierCode: str | None = None
    serviceCode: str | None = None
    shipDate: str | None = None
    voided: bool = False


class ShipStationRate(BaseModel):
    serviceName: str
    serviceCode: str
    shipmentCost: float
    otherCost: float
    carrierCode: str | None = None


# --- ShipStation API Client ---

class ShipStationAPI:
    def __init__(self, api_key: str, api_secret: str):
        self.base_url = "https://ssapi.shipstation.com"
        creds = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/json",
        }
        self._cache = LRUCache(max_size=100, ttl_seconds=30)
        self._rate_limit_remaining = 40
        self._rate_limit_reset = 0.0

    async def _request(self, method: str, endpoint: str, params: dict | None = None, json_data: dict | None = None) -> dict:
        if self._rate_limit_remaining <= 1 and time.time() < self._rate_limit_reset:
            wait = self._rate_limit_reset - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
        url = f"{self.base_url}{endpoint}"
        async with httpx.AsyncClient() as client:
            for attempt in range(3):
                try:
                    resp = await client.request(
                        method, url, headers=self.headers,
                        params=params, json=json_data, timeout=30.0,
                    )
                    self._rate_limit_remaining = int(resp.headers.get("X-Rate-Limit-Remaining", 40))
                    reset = resp.headers.get("X-Rate-Limit-Reset")
                    if reset:
                        self._rate_limit_reset = time.time() + int(reset)
                    if resp.status_code == 429:
                        wait = int(resp.headers.get("Retry-After", 30))
                        await asyncio.sleep(wait)
                        continue
                    resp.raise_for_status()
                    return resp.json()
                except httpx.HTTPStatusError:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2 ** attempt)

    async def list_orders(self, status: str | None = None, page: int = 1, page_size: int = 100,
                          order_number: str | None = None, tag_id: int | None = None,
                          customer_name: str | None = None) -> dict:
        params: dict = {"page": page, "pageSize": page_size}
        if status:
            params["orderStatus"] = status
        if order_number:
            params["orderNumber"] = order_number
        if tag_id:
            params["tagId"] = tag_id
        if customer_name:
            params["customerName"] = customer_name
        data = await self._request("GET", "/orders", params=params)
        orders = [ShipStationOrder(**o) for o in data.get("orders", [])]
        return {"orders": orders, "total": data.get("total", 0), "page": data.get("page", 1), "pages": data.get("pages", 1)}

    async def get_order(self, order_id: int) -> ShipStationOrder:
        data = await self._request("GET", f"/orders/{order_id}")
        return ShipStationOrder(**data)

    async def get_order_by_number(self, order_number: str) -> ShipStationOrder | None:
        result = await self.list_orders(order_number=order_number)
        return result["orders"][0] if result["orders"] else None

    async def create_order(self, order: ShipStationOrder) -> ShipStationOrder:
        data = await self._request("POST", "/orders/createorder", json_data=order.model_dump(exclude_none=True))
        return ShipStationOrder(**data)

    async def validate_address(self, address: ShipStationAddress) -> dict:
        data = await self._request("POST", "/shipments/getrates", json_data={
            "carrierCode": "stamps_com", "fromPostalCode": "77015",
            "toState": address.state, "toCountry": address.country or "US",
            "toPostalCode": address.postalCode, "toCity": address.city,
            "weight": {"value": 1, "units": "pounds"},
        })
        return {"valid": True, "rates_available": len(data) > 0}

    async def get_rates(self, carrier_code: str, from_postal: str, to_state: str,
                        to_postal: str, to_country: str, weight_value: float,
                        weight_units: str = "pounds", length: float | None = None,
                        width: float | None = None, height: float | None = None) -> list[ShipStationRate]:
        payload: dict = {
            "carrierCode": carrier_code, "fromPostalCode": from_postal,
            "toState": to_state, "toCountry": to_country, "toPostalCode": to_postal,
            "weight": {"value": weight_value, "units": weight_units},
        }
        if length and width and height:
            payload["dimensions"] = {"length": length, "width": width, "height": height, "units": "inches"}
        data = await self._request("POST", "/shipments/getrates", json_data=payload)
        return [ShipStationRate(**r) for r in data]

    async def create_label(self, order_id: int, carrier_code: str, service_code: str,
                           ship_date: str, weight_value: float, weight_units: str = "pounds",
                           test_label: bool = False) -> dict:
        payload = {
            "orderId": order_id, "carrierCode": carrier_code, "serviceCode": service_code,
            "shipDate": ship_date, "weight": {"value": weight_value, "units": weight_units},
            "testLabel": test_label,
        }
        return await self._request("POST", "/orders/createlabelfororder", json_data=payload)

    async def list_shipments(self, order_id: int | None = None, tracking_number: str | None = None,
                             page: int = 1) -> list[ShipStationShipment]:
        params: dict = {"page": page, "pageSize": 100}
        if order_id:
            params["orderId"] = order_id
        if tracking_number:
            params["trackingNumber"] = tracking_number
        data = await self._request("GET", "/shipments", params=params)
        return [ShipStationShipment(**s) for s in data.get("shipments", [])]

    async def get_tags(self) -> list[dict]:
        return await self._request("GET", "/accounts/listtags")

    async def add_tag(self, order_id: int, tag_id: int) -> dict:
        return await self._request("POST", "/orders/addtag", json_data={"orderId": order_id, "tagId": tag_id})
