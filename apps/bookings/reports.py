"""Guide collection report — PRD §5.7 / Phase 6.

One-page PDF per package listing every active booking's room number,
customer name & mobile, total pax, paid and due amounts, with a totals row.
The guide (no system access) uses the printed/PDF copy on the ship to
collect outstanding dues. Reuses the invoice PDF stack (fpdf2 + Noto fonts).
"""

from decimal import Decimal

from django.utils import timezone
from fpdf import FPDF

from .branding import draw_header_logo, draw_signature_block, draw_watermark
from .invoices import FONTS_DIR
from .models import Booking

NAVY = (16, 46, 80)
ZEBRA = (247, 249, 251)
GREY = (105, 115, 125)
RULE = (210, 216, 222)


def generate_guide_report_pdf(package):
    bookings = (
        Booking.objects.filter(package=package)
        .exclude(status=Booking.Status.CANCELLED)
        .select_related("room")
        .order_by("room__room_number")
    )

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.add_font("NotoSans", "", FONTS_DIR / "NotoSans-Regular.ttf")
    pdf.add_font("NotoSans", "B", FONTS_DIR / "NotoSans-Bold.ttf")
    pdf.add_font("NotoSansBengali", "", FONTS_DIR / "NotoSansBengali-Regular.ttf")
    pdf.add_font("NotoSansBengali", "B", FONTS_DIR / "NotoSansBengali-Bold.ttf")
    pdf.set_fallback_fonts(["NotoSansBengali"])
    pdf.set_text_shaping(True)
    epw = pdf.epw

    # Header — light, logo shown directly for a clean sharp mark
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, pdf.w, 2.5, "F")  # slim brand bar
    draw_header_logo(pdf, pdf.l_margin, 6, 17)
    pdf.set_text_color(*NAVY)
    pdf.set_xy(pdf.l_margin + 22, 10)
    pdf.set_font("NotoSans", "B", 15)
    pdf.cell(epw / 2 - 22, 8, f"{package.ship.name} — Guide Collection Report")
    pdf.set_font("NotoSans", "", 9)
    pdf.set_text_color(*GREY)
    pdf.cell(
        epw / 2, 8,
        f"{package.start_date:%d %b %Y} – {package.end_date:%d %b %Y}",
        align="R", new_x="LMARGIN", new_y="NEXT",
    )
    pdf.set_draw_color(*NAVY)
    pdf.set_line_width(0.5)
    pdf.line(pdf.l_margin, 26, pdf.l_margin + epw, 26)
    pdf.set_line_width(0.2)

    pdf.set_y(32)
    pdf.set_text_color(*GREY)
    pdf.set_font("NotoSans", "", 8.5)
    title = package.marketing_title or "Ship tour package"
    generated = f"{timezone.localtime(timezone.now()):%d %b %Y, %I:%M %p}"
    pdf.cell(epw / 2, 6, f"Package: {title}")
    pdf.cell(epw / 2, 6, f"Generated: {generated}", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # Table header
    col = {
        "room": 18, "name": epw - 18 - 34 - 14 - 32 - 32,
        "phone": 34, "pax": 14, "paid": 32, "due": 32,
    }
    pdf.set_font("NotoSans", "B", 8.5)
    pdf.set_fill_color(*NAVY)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(col["room"], 7, " Room", fill=True)
    pdf.cell(col["name"], 7, " Customer name", fill=True)
    pdf.cell(col["phone"], 7, " Mobile", fill=True)
    pdf.cell(col["pax"], 7, "Pax", fill=True, align="C")
    pdf.cell(col["paid"], 7, "Paid (BDT) ", fill=True, align="R")
    pdf.cell(col["due"], 7, "Due (BDT) ", fill=True, align="R", new_x="LMARGIN", new_y="NEXT")

    # Rows
    total_paid = total_due = Decimal("0.00")
    total_pax = 0
    pdf.set_font("NotoSans", "", 9)
    pdf.set_text_color(40, 40, 40)
    for i, booking in enumerate(bookings):
        pdf.set_fill_color(*(ZEBRA if i % 2 == 0 else (255, 255, 255)))
        pdf.cell(col["room"], 7, f" {booking.room.room_number}", fill=True)
        pdf.cell(col["name"], 7, f" {booking.customer_name}", fill=True)
        pdf.cell(col["phone"], 7, f" {booking.phone}", fill=True)
        pdf.cell(col["pax"], 7, str(booking.total_pax), fill=True, align="C")
        pdf.cell(col["paid"], 7, f"{booking.paid_amount} ", fill=True, align="R")
        pdf.set_font("NotoSans", "B" if booking.due_amount > 0 else "", 9)
        pdf.cell(col["due"], 7, f"{booking.due_amount} ", fill=True, align="R",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("NotoSans", "", 9)
        total_paid += booking.paid_amount
        total_due += booking.due_amount
        total_pax += booking.total_pax

    if not bookings:
        pdf.set_font("NotoSans", "", 9)
        pdf.cell(epw, 10, "No active bookings for this package.", align="C",
                 new_x="LMARGIN", new_y="NEXT")

    # Totals row
    pdf.set_draw_color(*RULE)
    pdf.set_font("NotoSans", "B", 9)
    pdf.cell(col["room"] + col["name"] + col["phone"], 8, " TOTAL", border="T")
    pdf.cell(col["pax"], 8, str(total_pax), border="T", align="C")
    pdf.cell(col["paid"], 8, f"{total_paid} ", border="T", align="R")
    pdf.set_text_color(*NAVY)
    pdf.cell(col["due"], 8, f"{total_due} ", border="T", align="R",
             new_x="LMARGIN", new_y="NEXT")

    # Authorized signature (right side, below the totals)
    pdf.ln(8)
    if pdf.get_y() > pdf.h - 58:
        pdf.add_page()
    sig_w = 70
    draw_signature_block(pdf, pdf.l_margin + epw - sig_w, sig_w, package.ship.name)

    # Footer note — placed right below the table (not pinned to page bottom,
    # which would trigger an unwanted blank second page).
    pdf.ln(4)
    pdf.set_font("NotoSans", "", 7.5)
    pdf.set_text_color(*GREY)
    pdf.cell(0, 5, "Bookings with due = 0.00 are fully paid. Collect the due amount "
                   "from each room and record it in the dashboard.", align="C",
             new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, f"{package.ship.name} · computer-generated report", align="C")

    draw_watermark(pdf, package.ship.name)
    return bytes(pdf.output())
