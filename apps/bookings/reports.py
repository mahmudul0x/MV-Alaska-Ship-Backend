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
from .models import Booking, BookingRoom

NAVY = (16, 46, 80)
ZEBRA = (247, 249, 251)
GREY = (105, 115, 125)
RULE = (210, 216, 222)


def generate_guide_report_pdf(package):
    # One row per cabin (a family holding several cabins appears once per room),
    # ordered by room number so the guide walks the ship in sequence. paid/due
    # belong to the whole booking, so they are printed once — on the booking's
    # first room here — and left blank on its other rooms, and the totals sum
    # per booking, never per room (otherwise a 3-cabin family's balance would be
    # counted three times).
    booking_rooms = (
        BookingRoom.objects.filter(package=package)
        .exclude(booking__status=Booking.Status.CANCELLED)
        .select_related("booking", "room")
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

    # Authority contact numbers — top-right corner, under the tour dates.
    # Per-ship, editable from the staff dashboard (falls back to the system
    # default). Skip the line entirely if none are configured.
    phones = package.ship.authority_phone_list
    if phones:
        pdf.set_font("NotoSans", "", 7.5)
        pdf.set_text_color(*GREY)
        pdf.set_xy(pdf.l_margin + epw / 2, 18)
        pdf.cell(epw / 2, 4, "Helpline: " + "  ·  ".join(phones), align="R")

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

    # Table header — Pax is split into Adults and Kids so the guide can see the
    # party composition per room at a glance (not just a combined count).
    col = {
        "room": 16, "name": epw - 16 - 30 - 15 - 15 - 30 - 30,
        "phone": 30, "adults": 15, "kids": 15, "paid": 30, "due": 30,
    }
    pdf.set_font("NotoSans", "B", 8.5)
    pdf.set_fill_color(*NAVY)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(col["room"], 7, " Room", fill=True)
    pdf.cell(col["name"], 7, " Customer name", fill=True)
    pdf.cell(col["phone"], 7, " Mobile", fill=True)
    pdf.cell(col["adults"], 7, "Adults", fill=True, align="C")
    pdf.cell(col["kids"], 7, "Kids", fill=True, align="C")
    pdf.cell(col["paid"], 7, "Paid (BDT) ", fill=True, align="R")
    pdf.cell(col["due"], 7, "Due (BDT) ", fill=True, align="R", new_x="LMARGIN", new_y="NEXT")

    # Rows
    total_paid = total_due = Decimal("0.00")
    total_adults = total_kids = 0
    seen_bookings = set()  # a booking's paid/due is counted once, on its 1st room
    pdf.set_font("NotoSans", "", 9)
    pdf.set_text_color(40, 40, 40)
    booking_rooms = list(booking_rooms)
    for i, br in enumerate(booking_rooms):
        booking = br.booking
        adults = br.adult_count
        kids = len(br.kid_details)
        first_room = booking.pk not in seen_bookings
        pdf.set_fill_color(*(ZEBRA if i % 2 == 0 else (255, 255, 255)))
        pdf.cell(col["room"], 7, f" {br.room.room_number}", fill=True)
        pdf.cell(col["name"], 7, f" {booking.customer_name}", fill=True)
        pdf.cell(col["phone"], 7, f" {booking.phone}", fill=True)
        pdf.cell(col["adults"], 7, str(adults), fill=True, align="C")
        pdf.cell(col["kids"], 7, str(kids), fill=True, align="C")
        # paid/due belong to the booking as a whole — print them on its first
        # room only, blank on the rest, so the guide reads one balance per party.
        paid_text = f"{booking.paid_amount} " if first_room else ""
        due_text = f"{booking.due_amount} " if first_room else ""
        pdf.cell(col["paid"], 7, paid_text, fill=True, align="R")
        pdf.set_font("NotoSans", "B" if (first_room and booking.due_amount > 0) else "", 9)
        pdf.cell(col["due"], 7, due_text, fill=True, align="R",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("NotoSans", "", 9)
        if first_room:
            seen_bookings.add(booking.pk)
            total_paid += booking.paid_amount
            total_due += booking.due_amount
        total_adults += adults
        total_kids += kids

    if not booking_rooms:
        pdf.set_font("NotoSans", "", 9)
        pdf.cell(epw, 10, "No active bookings for this package.", align="C",
                 new_x="LMARGIN", new_y="NEXT")

    # Totals row
    pdf.set_draw_color(*RULE)
    pdf.set_font("NotoSans", "B", 9)
    pdf.cell(col["room"] + col["name"] + col["phone"], 8, " TOTAL", border="T")
    pdf.cell(col["adults"], 8, str(total_adults), border="T", align="C")
    pdf.cell(col["kids"], 8, str(total_kids), border="T", align="C")
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
