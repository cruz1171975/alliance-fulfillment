"""Generate 4x6 packing slip PDFs for Zebra ZP 505 thermal printer."""
import io
from datetime import datetime, timezone
from reportlab.lib.pagesizes import inch
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from fulfillment.models import QueuedOrder


# ZP 505 label: 4" x 6"
PAGE_WIDTH = 4 * inch
PAGE_HEIGHT = 6 * inch


def generate_packing_slip(order: QueuedOrder, shipstation_order: dict | None = None) -> bytes:
    """Generate a 4x6 packing slip PDF. Returns PDF bytes.

    Args:
        order: QueuedOrder from our DB (has line_items, customer, etc.)
        shipstation_order: Optional full ShipStation order dict for extra details
            (ship-to address, etc.)
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=(PAGE_WIDTH, PAGE_HEIGHT),
        leftMargin=0.2 * inch,
        rightMargin=0.2 * inch,
        topMargin=0.2 * inch,
        bottomMargin=0.2 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = styles["Heading1"]
    title_style.fontSize = 14
    title_style.leading = 16
    title_style.spaceAfter = 4

    normal = styles["Normal"]
    normal.fontSize = 9
    normal.leading = 11

    bold_style = styles["Heading2"]
    bold_style.fontSize = 10
    bold_style.leading = 12
    bold_style.spaceAfter = 2
    bold_style.spaceBefore = 6

    small = styles["Normal"].clone("small")
    small.fontSize = 8
    small.leading = 10

    elements = []

    # Header
    elements.append(Paragraph("ALLIANCE CHEMICAL", title_style))
    elements.append(Paragraph(f"Order #{order.order_number}", bold_style))

    # Date
    order_date_str = order.order_date.strftime("%m/%d/%Y")
    elements.append(Paragraph(f"Date: {order_date_str}", small))
    elements.append(Spacer(1, 6))

    # Ship To
    elements.append(Paragraph("<b>SHIP TO:</b>", normal))
    if shipstation_order and shipstation_order.get("shipTo"):
        ship_to = shipstation_order["shipTo"]
        if ship_to.get("name"):
            elements.append(Paragraph(ship_to["name"], normal))
        if ship_to.get("company"):
            elements.append(Paragraph(ship_to["company"], normal))
        if ship_to.get("street1"):
            elements.append(Paragraph(ship_to["street1"], normal))
        if ship_to.get("street2"):
            elements.append(Paragraph(ship_to["street2"], normal))
        city_line = f"{ship_to.get('city', '')}, {ship_to.get('state', '')} {ship_to.get('postalCode', '')}"
        elements.append(Paragraph(city_line.strip(), normal))
    else:
        elements.append(Paragraph(order.customer_name, normal))
        if order.ship_to_state:
            elements.append(Paragraph(order.ship_to_state, normal))

    elements.append(Spacer(1, 8))

    # Items table
    elements.append(Paragraph("<b>ITEMS:</b>", normal))
    elements.append(Spacer(1, 4))

    table_data = [["Qty", "Item", "SKU"]]
    for item in order.line_items:
        # Shorten name if too long
        name = item.name or ""
        if len(name) > 35:
            name = name[:32] + "..."
        table_data.append([
            str(item.quantity),
            name,
            item.sku or "",
        ])

    col_widths = [0.4 * inch, 2.2 * inch, 0.9 * inch]
    table = Table(table_data, colWidths=col_widths)
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("LEADING", (0, 0), (-1, -1), 10),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 2),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    elements.append(table)

    # Footer
    elements.append(Spacer(1, 10))
    now_str = datetime.now(timezone.utc).strftime("%m/%d/%Y %I:%M %p UTC")
    elements.append(Paragraph(f"Printed: {now_str}", small))

    doc.build(elements)
    return buf.getvalue()
