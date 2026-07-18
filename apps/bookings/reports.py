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
# Group accent — the vertical bar + badge that marks a multi-room (family)
# booking, so the guide sees at a glance which cabins are one party / one bill.
GROUP_BAR = (198, 160, 74)  # gold
GROUP_TINT = (250, 246, 236)  # faint gold wash behind a group's rows


def _grouped_booking_rows(package):
    """Active BookingRooms for the package, grouped by booking so a family's
    cabins stay together, then ordered by their lowest room number so the guide
    still walks the ship roughly in sequence.

    Returns a list of groups, each: {"booking", "rooms": [BookingRoom, ...]}.
    Single-room bookings are just a group of one — rendered exactly as before.
    """
    booking_rooms = (
        BookingRoom.objects.filter(package=package)
        .exclude(booking__status=Booking.Status.CANCELLED)
        .select_related("booking", "room")
        .order_by("room__room_number")
    )
    groups = {}
    for br in booking_rooms:
        groups.setdefault(br.booking_id, {"booking": br.booking, "rooms": []})
        groups[br.booking_id]["rooms"].append(br)
    # Order groups by each booking's lowest room number (numeric-aware), so a
    # 2-cabin family sits at its first cabin's position in the walk-through.
    def sort_key(group):
        first = group["rooms"][0].room.room_number
        return (len(first), first)  # short-then-lexical ≈ numeric for room nos.

    return sorted(groups.values(), key=sort_key)


def generate_guide_report_pdf(package):
    # One row per cabin, but a family's cabins are kept TOGETHER and banded so
    # the guide reads them as one party with one bill — not scattered across the
    # sheet by room number with a blank balance on the second cabin (which read
    # as "nothing owed"). paid/due belong to the whole booking, so a multi-room
    # booking prints them once, on a per-group subtotal line; a single-room
    # booking is unchanged. Totals sum per booking, never per room.
    groups = _grouped_booking_rows(package)

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
    row_h = 7
    # x where the group accent bar is drawn (just inside the left margin).
    bar_x = pdf.l_margin
    zebra_i = 0  # zebra alternates per printed row, across groups
    pdf.set_font("NotoSans", "", 9)
    pdf.set_text_color(40, 40, 40)

    def money_row(label_cells, paid, due, *, bold_due, fill_rgb, top_border=False):
        """Draw one table row; label_cells is [(width, text, align), ...] that
        together span room+name+phone+adults+kids."""
        border = "T" if top_border else 0
        pdf.set_fill_color(*fill_rgb)
        for width, text, align in label_cells:
            pdf.cell(width, row_h, text, fill=True, align=align, border=border)
        pdf.cell(col["paid"], row_h, paid, fill=True, align="R", border=border)
        pdf.set_font("NotoSans", "B" if bold_due else "", 9)
        pdf.cell(col["due"], row_h, due, fill=True, align="R", border=border,
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("NotoSans", "", 9)

    for group in groups:
        booking = group["booking"]
        rooms = group["rooms"]
        is_group = len(rooms) > 1
        group_top_y = pdf.get_y()

        for r_idx, br in enumerate(rooms):
            adults = br.adult_count
            kids = len(br.kid_details)
            # A multi-room booking gets a faint gold wash so its cabins read as
            # one block; single-room bookings keep the plain zebra striping.
            fill_rgb = (
                GROUP_TINT if is_group else (ZEBRA if zebra_i % 2 == 0 else (255, 255, 255))
            )
            # First cabin of a group carries a "· N rooms" tag after the name so
            # the guide instantly sees the party spans several cabins.
            name = booking.customer_name
            if is_group and r_idx == 0:
                name = f"{name}  · {len(rooms)} rooms"
            label_cells = [
                (col["room"], f" {br.room.room_number}", "L"),
                (col["name"], f" {name}", "L"),
                (col["phone"], f" {booking.phone}", "L"),
                (col["adults"], str(adults), "C"),
                (col["kids"], str(kids), "C"),
            ]
            if is_group:
                # Balance shows on the group SUBTOTAL line below, not per cabin.
                money_row(label_cells, "", "", bold_due=False, fill_rgb=fill_rgb)
            else:
                # Single room: balance sits right on the row, exactly as before.
                money_row(
                    label_cells,
                    f"{booking.paid_amount} ",
                    f"{booking.due_amount} ",
                    bold_due=booking.due_amount > 0,
                    fill_rgb=fill_rgb,
                )
                zebra_i += 1
            total_adults += adults
            total_kids += kids

        if is_group:
            # Group subtotal line: the one place this family's paid/due appears.
            subtotal_label = [
                (col["room"], "", "L"),
                (
                    col["name"] + col["phone"] + col["adults"] + col["kids"],
                    f"   ↳ Booking {booking.booking_code} — combined balance",
                    "L",
                ),
            ]
            pdf.set_font("NotoSans", "B", 8.5)
            money_row(
                subtotal_label,
                f"{booking.paid_amount} ",
                f"{booking.due_amount} ",
                bold_due=booking.due_amount > 0,
                fill_rgb=GROUP_TINT,
            )
            pdf.set_font("NotoSans", "", 9)
            # Gold accent bar down the left edge spanning every row of the group.
            group_bottom_y = pdf.get_y()
            pdf.set_fill_color(*GROUP_BAR)
            pdf.rect(bar_x, group_top_y, 1.4, group_bottom_y - group_top_y, "F")

        total_paid += booking.paid_amount
        total_due += booking.due_amount

    if not groups:
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
    pdf.cell(0, 5, "Rooms marked with a gold bar and \"· N rooms\" belong to ONE "
                   "family booking — collect the combined balance once, not per room.",
             align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, f"{package.ship.name} · computer-generated report", align="C")

    draw_watermark(pdf, package.ship.name)
    return bytes(pdf.output())
