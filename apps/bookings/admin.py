from django.contrib import admin, messages
from django.core.files.base import ContentFile
from django.utils.html import format_html

from .invoices import generate_invoice_pdf, invoice_number, send_invoice_email
from .models import Booking, BookingStatusLog, Invoice, Payment


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    can_delete = False
    readonly_fields = (
        "amount",
        "payment_type",
        "gateway",
        "transaction_id",
        "status",
        "paid_at",
    )
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        return False


class BookingStatusLogInline(admin.TabularInline):
    model = BookingStatusLog
    extra = 0
    can_delete = False
    readonly_fields = ("old_status", "new_status", "changed_by", "created_at")
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        "booking_code",
        "customer_name",
        "phone",
        "package",
        "room",
        "total_pax",
        "total_amount",
        "paid_amount",
        "due_amount",
        "status",
    )
    list_filter = ("status", "package")
    search_fields = ("booking_code", "customer_name", "phone", "email")
    # Amounts are always computed server-side (pricing service / payments),
    # never entered by hand.
    readonly_fields = ("booking_code", "total_amount", "paid_amount", "due_amount")
    inlines = [PaymentInline, BookingStatusLogInline]
    fieldsets = (
        ("Customer", {"fields": ("customer_name", "phone", "email")}),
        ("Trip", {"fields": ("package", "room", "adult_count", "kid_details")}),
        (
            "Amounts (auto-calculated)",
            {"fields": ("total_amount", "paid_amount", "due_amount")},
        ),
        ("Status", {"fields": ("status", "booking_code")}),
    )

    def save_model(self, request, obj, form, change):
        obj.save(changed_by=request.user)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "booking",
        "amount",
        "payment_type",
        "gateway",
        "status",
        "paid_at",
    )
    list_filter = ("status", "payment_type", "gateway")
    search_fields = ("booking__booking_code", "transaction_id")
    readonly_fields = ("gateway_payload",)


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("booking", "sent_via", "sent_at", "pdf_link")
    list_filter = ("sent_via",)
    search_fields = ("booking__booking_code",)
    actions = ["resend_email", "regenerate_pdf"]

    def has_add_permission(self, request):
        return False

    @admin.display(description="PDF")
    def pdf_link(self, invoice):
        if invoice.pdf_file:
            return format_html(
                '<a href="{}" target="_blank">Download</a>', invoice.pdf_file.url
            )
        return "—"

    @admin.action(description="Resend invoice email")
    def resend_email(self, request, queryset):
        sent = 0
        for invoice in queryset.select_related("booking"):
            try:
                send_invoice_email(invoice)
                sent += 1
            except Exception as exc:
                self.message_user(
                    request, f"{invoice}: {exc}", level=messages.ERROR
                )
        self.message_user(request, f"{sent} invoice email(s) sent.")

    @admin.action(description="Regenerate PDF")
    def regenerate_pdf(self, request, queryset):
        for invoice in queryset.select_related("booking"):
            invoice.pdf_file.save(
                f"{invoice_number(invoice)}.pdf",
                ContentFile(generate_invoice_pdf(invoice)),
                save=True,
            )
        self.message_user(request, f"{queryset.count()} PDF(s) regenerated.")
