# Design: Per-Item Restock Quantities + Print All Slips

**Date:** 2026-03-10
**Status:** Approved

## Summary

Two changes to the picker interface:

1. Replace the global "Flag Low Stock" section with per-line-item restock quantity inputs on each order card
2. Replace individual "Print Slip" buttons with a single "Print All Slips" button for the batch

## Change 1: Per-Item Restock Quantity

Each order card shows line items (e.g., `2 x Acetone - 1 Gallon`). Next to each line item, add a small number input field (placeholder: "Restock qty") and a submit button.

**Picker workflow:**
1. Picker sees stock is low for an item while picking
2. Enters the quantity needed to restock in the input field
3. Submits — POST to new endpoint with picker ID, order ID, item SKU, item name, and restock quantity
4. Backend inserts into `stock_alerts` table (extended with `restock_qty` and `order_id` columns)
5. SMS sent to manager: `"RESTOCK NEEDED: 10 x Acetone - 1 Gallon (Order #1234, flagged by Maria)"`
6. Visual feedback on the item (checkmark/highlight) confirms submission

**Manager dashboard:** Existing Low Stock Alerts section shows these alerts with the restock quantity included.

## Change 2: Remove Global "Flag Low Stock"

The text input and "Send" button at the bottom of the picker screen are removed entirely. Per-item restock covers the use case.

## Change 3: Print All Button

- Remove individual "Print Slip" buttons from each order card
- Add a single "Print All Slips" button at the top of the batch area (near "Get Next Batch")
- Clicking it opens each order's packing slip PDF in a separate browser tab
- Uses the existing `GET /api/orders/{id}/packing-slip` endpoint for each order

## Database Changes

`stock_alerts` table gets two new columns:
- `restock_qty` (integer, default 0) — how many units needed
- `order_id` (integer, nullable) — which order the picker was working when they flagged it

## API Changes

- Modify `POST /api/alerts/stock` to accept `restock_qty` (int) and `order_id` (int) in addition to existing fields
- SMS message format updated to include restock quantity and order number

## No Changes To

- Packing slip PDF format
- Manager dashboard layout (alerts section already shows product + picker + timestamp; extended to show qty)
- Queue engine, sync, or batch logic
