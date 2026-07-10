"""Invoice PDF generation + email delivery.

Sent after every successful payment with the running paid/due totals
(PRD §5.6). PDF text is shaped with HarfBuzz (fpdf2), so Bengali and English
both render correctly using the bundled Noto fonts in backend/assets/fonts.

total/paid/due always come from the Booking's stored fields (the money
truth); the per-pax breakdown lines are informational.
"""

import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone
from django.utils.html import escape
from fpdf import FPDF

from .branding import draw_header_logo, draw_signature_block, draw_watermark
from .models import Invoice, Payment
from .pricing import price_breakdown

logger = logging.getLogger(__name__)

FONTS_DIR = settings.BASE_DIR / "assets" / "fonts"


def create_and_send_invoice(booking):
    """Create an Invoice (row + PDF) and email it. Never raises — payment
    processing must not be disturbed by invoice/email trouble."""
    invoice = Invoice.objects.create(booking=booking, sent_via=Invoice.SentVia.EMAIL)
    try:
        pdf_bytes = generate_invoice_pdf(invoice)
        invoice.pdf_file.save(
            f"{invoice_number(invoice)}.pdf", ContentFile(pdf_bytes), save=True
        )
    except Exception:
        logger.exception("Invoice PDF generation failed for %s", booking.booking_code)
        return invoice
    try:
        send_invoice_email(invoice)
    except Exception:
        # sent_at stays NULL — picked up later by `send_unsent_invoices`.
        logger.exception("Invoice email failed for %s", booking.booking_code)
    return invoice


def invoice_number(invoice):
    return f"INV-{invoice.booking.booking_code}-{invoice.pk}"


def send_invoice_email(invoice):
    booking = invoice.booking
    ship_name = booking.package.ship.name
    fully_paid = booking.due_amount <= 0
    subject = f"{ship_name} — Invoice for booking {booking.booking_code}"
    # Plain-text fallback for clients that don't render HTML.
    body = (
        f"Dear {booking.customer_name},\n\n"
        f"Thank you for your payment. Your invoice is attached.\n\n"
        f"Booking: {booking.booking_code}\n"
        f"Room: {booking.room.room_number}\n"
        f"Total: {booking.total_amount} BDT\n"
        f"Paid: {booking.paid_amount} BDT\n"
        f"Due: {booking.due_amount} BDT"
        + ("\n\nYour booking is PAID IN FULL." if fully_paid else "")
        + f"\n\n{ship_name}"
    )
    message = EmailMultiAlternatives(subject=subject, body=body, to=[booking.email])
    message.attach_alternative(_invoice_email_html(invoice), "text/html")
    invoice.pdf_file.open("rb")
    try:
        message.attach(
            f"{invoice_number(invoice)}.pdf", invoice.pdf_file.read(), "application/pdf"
        )
    finally:
        invoice.pdf_file.close()
    message.send()
    invoice.sent_at = timezone.now()
    invoice.save(update_fields=["sent_at"])


def _invoice_email_html(invoice):
    """Branded HTML body — email-safe markup only (tables + inline styles),
    since email clients ignore stylesheets and block remote assets."""
    booking = invoice.booking
    package = booking.package
    ship_name = escape(package.ship.name)
    fully_paid = booking.due_amount <= 0

    label = 'style="padding:9px 0;color:#69737d;font-size:13px;width:38%;"'
    value = 'style="padding:9px 0;color:#28323c;font-size:13px;font-weight:bold;"'
    details = "".join(
        f'<tr style="border-bottom:1px solid #eef1f5;">'
        f"<td {label}>{escape(k)}</td><td {value}>{escape(str(v))}</td></tr>"
        for k, v in [
            ("Booking code", booking.booking_code),
            ("Room", f"{booking.room.room_number} ({booking.room.room_type.name})"),
            ("Tour dates",
             f"{package.start_date:%d %b %Y} – {package.end_date:%d %b %Y}"),
            ("Guests",
             f"{booking.adult_count} adult(s), {len(booking.kid_details)} kid(s)"),
        ]
    )

    amount = 'style="padding:7px 14px;font-size:13px;text-align:right;color:#28323c;"'
    money = "".join(
        f'<tr><td style="padding:7px 14px;font-size:13px;color:#69737d;">{k}</td>'
        f"<td {amount}>{v} BDT</td></tr>"
        for k, v in [
            ("Total amount", booking.total_amount),
            ("Paid so far", booking.paid_amount),
        ]
    )
    due_row = (
        '<tr style="background:#102e50;">'
        '<td style="padding:9px 14px;font-size:13px;color:#ffffff;font-weight:bold;">'
        "Due amount</td>"
        '<td style="padding:9px 14px;font-size:14px;text-align:right;color:#ffffff;'
        f'font-weight:bold;">{booking.due_amount} BDT</td></tr>'
    )
    if fully_paid:
        status_banner = (
            '<div style="background:#157333;color:#ffffff;text-align:center;'
            'padding:11px;border-radius:5px;font-size:14px;font-weight:bold;">'
            "PAID IN FULL — সম্পূর্ণ পরিশোধিত</div>"
        )
    else:
        status_banner = (
            '<div style="background:#fdf3e0;color:#8a5a10;text-align:center;'
            'padding:11px;border-radius:5px;font-size:13px;">'
            f"Remaining balance: <strong>{booking.due_amount} BDT</strong> — "
            "please clear the due amount before your tour date.</div>"
        )

    return f"""\
<div style="background:#eef1f5;padding:28px 12px;font-family:Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="max-width:600px;margin:0 auto;background:#ffffff;border:1px solid #dfe4ea;
                border-radius:8px;border-collapse:separate;overflow:hidden;">
    <tr>
      <td style="background:#102e50;padding:24px 30px;">
        <div style="color:#ffffff;font-size:22px;font-weight:bold;">{ship_name}</div>
        <div style="color:#c2d0e0;font-size:11px;letter-spacing:2px;margin-top:3px;">
          SHIP TOUR PACKAGE BOOKING</div>
      </td>
    </tr>
    <tr>
      <td style="padding:26px 30px 0;color:#28323c;font-size:14px;line-height:1.65;">
        Dear <strong>{escape(booking.customer_name)}</strong>,<br><br>
        Thank you for your payment. Your official invoice
        <strong>{invoice_number(invoice)}</strong> is attached to this email as a PDF.
      </td>
    </tr>
    <tr>
      <td style="padding:20px 30px 0;">
        <div style="color:#102e50;font-size:12px;font-weight:bold;letter-spacing:1px;
                    border-bottom:2px solid #102e50;padding-bottom:5px;">
          BOOKING DETAILS</div>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
               style="border-collapse:collapse;">{details}</table>
      </td>
    </tr>
    <tr>
      <td style="padding:22px 30px 0;">
        <div style="color:#102e50;font-size:12px;font-weight:bold;letter-spacing:1px;
                    border-bottom:2px solid #102e50;padding-bottom:5px;">
          PAYMENT SUMMARY</div>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
               style="border-collapse:collapse;background:#f7f9fb;">{money}{due_row}
        </table>
      </td>
    </tr>
    <tr><td style="padding:18px 30px 0;">{status_banner}</td></tr>
    <tr>
      <td style="padding:24px 30px 26px;color:#69737d;font-size:12px;line-height:1.6;
                 border-top:1px solid #eef1f5;text-align:center;">
        আপনার ভ্রমণ আনন্দময় হোক — Thank you for sailing with {ship_name}.<br>
        <span style="font-size:11px;">This is an automated email; the attached invoice
        bears the official seal of {ship_name}.</span>
      </td>
    </tr>
  </table>
</div>
"""


# Condensed from the company's official Payment & Cancellation Policy —
# printed on every invoice; the full version lives on the website's policy page.
POLICY_POINTS = [
    "Booking confirmation requires a 50% advance payment; the remaining balance "
    "must be settled before the journey.",
    "Cancellation charges (% of total amount) — individual bookings: 3 weeks "
    "before departure 5%, 2 weeks 15%, 1 week 35%, 3 days 50%, 48 hours 75%, "
    "24 hours 90%, less than 24 hours 100%.",
    "Group bookings: 15%, 20%, 25%, 50%, 70%, 90% and 100% on the same schedule.",
    "All prices exclude VAT & TAX. Additional government revenue charges apply "
    "for foreign guests.",
    "In case of bad weather, technical problems of the ship, or fewer than 30 "
    "passengers, the tour may be cancelled, rescheduled or refunded upon "
    "discussion with the guest.",
]


def _draw_policy_panel(pdf, x, y, width):
    """Compact policy summary beside the totals box. Returns its end y."""
    NAVY = (16, 46, 80)
    GREY = (105, 115, 125)
    RULE = (210, 216, 222)
    pdf.set_xy(x, y)
    pdf.set_font("NotoSans", "B", 8)
    pdf.set_text_color(*NAVY)
    pdf.cell(width, 5, "IMPORTANT POLICIES", new_x="LEFT", new_y="NEXT")
    pdf.set_draw_color(*RULE)
    pdf.line(x, pdf.get_y() + 0.5, x + width, pdf.get_y() + 0.5)
    pdf.ln(2.5)
    pdf.set_font("NotoSans", "", 6.8)
    pdf.set_text_color(*GREY)
    for point in POLICY_POINTS:
        pdf.set_x(x)
        pdf.multi_cell(width, 3.3, f"•  {point}", new_x="LEFT", new_y="NEXT")
        pdf.ln(1.2)
    return pdf.get_y()


def generate_invoice_pdf(invoice):
    booking = invoice.booking
    package = booking.package

    NAVY = (16, 46, 80)
    ACCENT = (232, 238, 245)
    ZEBRA = (247, 249, 251)
    GREY = (105, 115, 125)
    GREEN = (21, 115, 51)
    RULE = (210, 216, 222)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.add_font("NotoSans", "", FONTS_DIR / "NotoSans-Regular.ttf")
    pdf.add_font("NotoSans", "B", FONTS_DIR / "NotoSans-Bold.ttf")
    pdf.add_font("NotoSansBengali", "", FONTS_DIR / "NotoSansBengali-Regular.ttf")
    pdf.add_font("NotoSansBengali", "B", FONTS_DIR / "NotoSansBengali-Bold.ttf")
    pdf.set_fallback_fonts(["NotoSansBengali"])
    pdf.set_text_shaping(True)
    epw = pdf.epw  # usable width between margins

    # ── Header — light, logo shown directly for a clean sharp mark ────────
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, pdf.w, 3, "F")  # slim brand bar
    draw_header_logo(pdf, pdf.l_margin, 8, 23)
    text_x = pdf.l_margin + 29
    pdf.set_text_color(*NAVY)
    pdf.set_xy(text_x, 10)
    pdf.set_font("NotoSans", "B", 20)
    pdf.cell(epw / 2 - 29, 10, package.ship.name)
    pdf.set_font("NotoSans", "B", 22)
    pdf.cell(epw / 2, 10, "INVOICE", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("NotoSans", "", 9)
    pdf.set_text_color(*GREY)
    pdf.set_x(text_x)
    pdf.cell(epw / 2 - 29, 6, "Ship Tour Package Booking")
    pdf.cell(epw / 2, 6, invoice_number(invoice), align="R")
    pdf.set_draw_color(*NAVY)
    pdf.set_line_width(0.5)
    pdf.line(pdf.l_margin, 34, pdf.l_margin + epw, 34)
    pdf.set_line_width(0.2)

    # ── Meta strip ─────────────────────────────────────────────────────────
    pdf.set_y(40)
    pdf.set_text_color(*GREY)
    pdf.set_font("NotoSans", "", 9)
    issued = f"{timezone.localtime(invoice.created_at):%d %b %Y, %I:%M %p}"
    pdf.cell(epw / 3, 6, f"Invoice date: {issued}")
    pdf.cell(epw / 3, 6, f"Booking: {booking.booking_code}", align="C")
    state = "PAID IN FULL" if booking.due_amount <= 0 else booking.get_status_display()
    pdf.cell(epw / 3, 6, f"Status: {state}", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*RULE)
    pdf.line(pdf.l_margin, pdf.get_y() + 1, pdf.l_margin + epw, pdf.get_y() + 1)
    pdf.ln(6)

    # ── Two-column info blocks ─────────────────────────────────────────────
    col_w = (epw - 8) / 2
    top_y = pdf.get_y()

    def info_block(x, title, rows):
        pdf.set_xy(x, top_y)
        pdf.set_font("NotoSans", "B", 10)
        pdf.set_text_color(*NAVY)
        pdf.set_fill_color(*ACCENT)
        pdf.cell(col_w, 7, f"  {title}", fill=True, new_x="LEFT", new_y="NEXT")
        pdf.set_text_color(40, 40, 40)
        for label, value in rows:
            pdf.set_x(x)
            pdf.set_font("NotoSans", "", 9)
            pdf.set_text_color(*GREY)
            pdf.cell(col_w * 0.34, 6.5, f"  {label}")
            pdf.set_font("NotoSans", "", 9.5)
            pdf.set_text_color(40, 40, 40)
            pdf.cell(col_w * 0.66, 6.5, str(value), new_x="LEFT", new_y="NEXT")

    info_block(
        pdf.l_margin,
        "BILLED TO",
        [
            ("Name", booking.customer_name),
            ("Phone", booking.phone),
            ("Email", booking.email),
        ],
    )
    end_left = pdf.get_y()
    info_block(
        pdf.l_margin + col_w + 8,
        "TRIP DETAILS",
        [
            ("Tour dates", f"{package.start_date:%d %b %Y} – {package.end_date:%d %b %Y}"),
            ("Room", f"{booking.room.room_number} ({booking.room.room_type.name})"),
            (
                "Guests",
                f"{booking.adult_count} adult(s), {len(booking.kid_details)} kid(s)",
            ),
        ],
    )
    pdf.set_y(max(end_left, pdf.get_y()) + 6)

    # ── Table helpers ──────────────────────────────────────────────────────
    def table_header(title, columns):
        pdf.set_font("NotoSans", "B", 10)
        pdf.set_text_color(*NAVY)
        pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("NotoSans", "B", 8.5)
        pdf.set_fill_color(*NAVY)
        pdf.set_text_color(255, 255, 255)
        for label, width, align in columns:
            pdf.cell(width, 7, f" {label}", fill=True, align=align)
        pdf.ln()

    def table_row(cells, shade):
        pdf.set_font("NotoSans", "", 9)
        pdf.set_text_color(40, 40, 40)
        pdf.set_fill_color(*(ZEBRA if shade else (255, 255, 255)))
        for value, width, align in cells:
            pdf.cell(width, 7, f" {value} ", fill=True, align=align)
        pdf.ln()

    # ── Charges table ──────────────────────────────────────────────────────
    desc_w, amt_w = epw - 45, 45
    table_header("CHARGES", [("Description", desc_w, "L"), ("Amount (BDT)", amt_w, "R")])
    try:
        breakdown = price_breakdown(
            booking.room.room_type, package, booking.adult_count, booking.kid_ages
        )
        rows = [
            (f"Room {booking.room.room_number} — base price", breakdown["room_base"]),
            (
                f"Adult fare ({breakdown['adult_count']} × {breakdown['adult_price']})",
                breakdown["adults_subtotal"],
            ),
        ] + [
            (f"Kid fare (age {kid['age']})", kid["charge"]) for kid in breakdown["kids"]
        ]
    except ValidationError:
        # Pricing rules changed since booking — the stored total still rules.
        rows = [("Package charges", booking.total_amount)]
    for i, (desc, amount) in enumerate(rows):
        table_row([(desc, desc_w, "L"), (f"{amount}", amt_w, "R")], shade=i % 2 == 0)
    pdf.ln(5)

    # ── Payments table ─────────────────────────────────────────────────────
    payments = booking.payments.filter(status=Payment.Status.SUCCESS).order_by(
        "paid_at", "pk"
    )
    if payments:
        d_w, t_w, y_w, a_w = 48, epw - 48 - 28 - 40, 28, 40
        table_header(
            "PAYMENTS RECEIVED",
            [
                ("Date", d_w, "L"),
                ("Transaction ID", t_w, "L"),
                ("Type", y_w, "L"),
                ("Amount (BDT)", a_w, "R"),
            ],
        )
        for i, payment in enumerate(payments):
            paid_at = (
                f"{timezone.localtime(payment.paid_at):%d %b %Y, %I:%M %p}"
                if payment.paid_at
                else "—"
            )
            table_row(
                [
                    (paid_at, d_w, "L"),
                    (payment.transaction_id, t_w, "L"),
                    (payment.get_payment_type_display(), y_w, "L"),
                    (f"{payment.amount}", a_w, "R"),
                ],
                shade=i % 2 == 0,
            )
        pdf.ln(5)

    # ── Important policies (left) + summary box (right) ───────────────────
    box_w = 80
    box_x = pdf.l_margin + epw - box_w
    column_top = pdf.get_y()
    panel_end = _draw_policy_panel(pdf, pdf.l_margin, column_top, epw - box_w - 12)
    pdf.set_y(column_top)

    def summary_row(label, value, bold=False, fill=None, text=None):
        pdf.set_x(box_x)
        pdf.set_font("NotoSans", "B" if bold else "", 9.5 if not bold else 10.5)
        pdf.set_text_color(*(text or (40, 40, 40)))
        pdf.set_fill_color(*(fill or (255, 255, 255)))
        pdf.cell(box_w * 0.5, 8, f" {label}", fill=fill is not None)
        pdf.cell(
            box_w * 0.5, 8, f"{value} ", align="R", fill=fill is not None,
            new_x="LMARGIN", new_y="NEXT",
        )

    summary_row("Total amount", booking.total_amount)
    summary_row("Paid so far", booking.paid_amount)
    summary_row(
        "Due amount", booking.due_amount, bold=True, fill=NAVY, text=(255, 255, 255)
    )

    if booking.due_amount <= 0:
        pdf.ln(3)
        pdf.set_x(box_x)
        pdf.set_font("NotoSans", "B", 11)
        pdf.set_text_color(255, 255, 255)
        pdf.set_fill_color(*GREEN)
        pdf.cell(box_w, 9, "PAID IN FULL — সম্পূর্ণ পরিশোধিত", align="C", fill=True,
                 new_x="LMARGIN", new_y="NEXT")

    # ── Authorized signature ───────────────────────────────────────────────
    pdf.ln(5)
    if pdf.get_y() > pdf.h - 70:
        pdf.add_page()
    draw_signature_block(pdf, box_x, box_w, package.ship.name)
    pdf.set_y(max(panel_end, pdf.get_y()))

    # ── Footer ─────────────────────────────────────────────────────────────
    pdf.set_y(-32)
    pdf.set_draw_color(*RULE)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + epw, pdf.get_y())
    pdf.ln(3)
    pdf.set_font("NotoSans", "", 9)
    pdf.set_text_color(*NAVY)
    pdf.cell(0, 6, "আপনার ভ্রমণ আনন্দময় হোক — Thank you for sailing with MV Alaska",
             align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("NotoSans", "", 7.5)
    pdf.set_text_color(*GREY)
    pdf.cell(0, 5, "This invoice is issued electronically and bears the official "
                   f"seal of {package.ship.name}.",
             align="C")

    draw_watermark(pdf, package.ship.name)
    return bytes(pdf.output())
