import logging

from django.conf import settings
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import payment_service, sslcommerz
from .models import Booking, Invoice, Payment
from .serializers import (
    BookingCreateSerializer,
    BookingInvoiceSerializer,
    BookingPublicSerializer,
    BookingQuoteSerializer,
    PaymentInitiateSerializer,
    breakdown_as_json,
)

logger = logging.getLogger(__name__)


class BookingViewSet(
    mixins.CreateModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    queryset = Booking.objects.select_related("package", "room")
    serializer_class = BookingPublicSerializer
    lookup_field = "booking_code"
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "booking"

    def get_throttles(self):
        # Per-action buckets so cheap reads never drain the strict
        # booking-creation budget (default "booking", 10/min):
        #   quote           — fired on every pax change in the wizard.
        #   retrieve        — the post-payment status poll (every 2s ×~6) plus
        #     confirmation-page reads; a 429 here shows a just-paid customer a
        #     stuck state, so it gets its own generous "status" bucket
        #     (QA phase8b F1).
        #   invoices        — read-only list of the customer's own invoices.
        # create and pay are mutations and keep the strict "booking" scope.
        if self.action == "quote":
            self.throttle_scope = "quote"
        elif self.action in ("retrieve", "invoices"):
            self.throttle_scope = "status"
        return super().get_throttles()

    def get_serializer_class(self):
        if self.action == "create":
            return BookingCreateSerializer
        if self.action == "quote":
            return BookingQuoteSerializer
        return BookingPublicSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = serializer.save()
        data = BookingPublicSerializer(booking).data
        data["price_breakdown"] = breakdown_as_json(serializer.get_breakdown())
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def quote(self, request):
        """Price preview — validates like create but writes nothing."""
        serializer = BookingQuoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(breakdown_as_json(serializer.get_breakdown()))

    @action(detail=True, methods=["get"])
    def invoices(self, request, booking_code=None):
        """The customer's own invoices for this booking.

        Authorised by the booking_code itself, which is how every other public
        booking endpoint works (it is unguessable and only the customer has
        it). Each entry carries a download_url bearing the invoice's own
        capability token — so knowing one booking's code never reveals another
        customer's invoice (QA C1). Previously the customer had no way at all
        to re-obtain their invoice; the UI's "Download Receipt" button just
        opened a browser print dialog on an HTML card.
        """
        booking = self.get_object()
        invoices = booking.invoices.exclude(pdf_file="").order_by("-created_at")
        return Response(
            BookingInvoiceSerializer(
                invoices, many=True, context={"request": request}
            ).data
        )

    @action(detail=True, methods=["post"])
    def pay(self, request, booking_code=None):
        """Start an SSLCommerz checkout session (full or partial payment)."""
        booking = self.get_object()
        serializer = PaymentInitiateSerializer(
            data=request.data, context={"booking": booking}
        )
        serializer.is_valid(raise_exception=True)
        payment, gateway_url = payment_service.initiate_payment(
            booking, **serializer.validated_data
        )
        return Response(
            {
                "gateway_url": gateway_url,
                "tran_id": payment.transaction_id,
                "amount": str(payment.amount),
                "payment_type": payment.payment_type,
            }
        )


class InvoiceDownloadView(APIView):
    """Serve an invoice PDF against its capability token.

    Replaces the old raw MEDIA_URL link, which was served by
    django.views.static.serve with no access check whatsoever and lived at a
    path derivable from the booking code plus a sequential integer — so any
    customer could enumerate everyone else's invoices (QA C1).

    The token is 256 bits of entropy, is not derived from anything public, and
    authorises exactly one invoice. It is what the customer's own emailed link
    carries.
    """

    def get(self, request, token):
        invoice = get_object_or_404(Invoice, access_token=token)
        if not invoice.pdf_file:
            raise Http404("This invoice has no PDF.")
        return FileResponse(
            invoice.pdf_file.open("rb"),
            content_type="application/pdf",
            filename=f"{invoice.number}.pdf",
        )


def _frontend_redirect(result, request):
    """302 to the frontend result page. The page must fetch the booking by
    code for the real status — redirect data is presentation-only."""
    payment = None
    tran_id = request.data.get("tran_id")
    if tran_id:
        payment = Payment.objects.filter(transaction_id=tran_id).first()
    booking_code = payment.booking.booking_code if payment else ""
    return redirect(f"{settings.FRONTEND_URL}/payment/{result}?booking={booking_code}")


class PaymentIPNView(APIView):
    """SSLCommerz server-to-server notification.

    Two independent defences:
    - Crediting: the POSTed data is only a trigger — the verdict comes from
      the authenticated Validation API call inside process_payment_result().
    - State-closing: a FAILED/CANCELLED status is only honoured when the
      IPN's verify_sign/verify_key hash proves it came from SSLCommerz.
      Without this, anyone who learns a tran_id (it appears in redirect URLs
      and browser history — it is not a secret) could kill a live payment
      session and strand the customer's in-flight money.

    Signature-verified notifications are answered 200 so the gateway stops
    retrying; unverified ones get 400 and change nothing.

    Throttling is deliberately disabled: the default anon throttle keys on a
    spoofable header, so an attacker could 429 genuine gateway IPNs and delay
    payment confirmations. Signature verification replaces rate limiting here.
    """

    throttle_classes = []

    def post(self, request):
        if not sslcommerz.verify_ipn_signature(request.data):
            logger.warning(
                "Rejected IPN with missing/invalid signature (tran_id=%s)",
                request.data.get("tran_id"),
            )
            return Response(
                {"detail": "Invalid IPN signature."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tran_id = request.data.get("tran_id")
        val_id = request.data.get("val_id")
        ipn_status = request.data.get("status")

        try:
            if ipn_status in ("FAILED",):
                payment_service.mark_payment_closed(tran_id, Payment.Status.FAILED)
            elif ipn_status in ("CANCELLED",):
                payment_service.mark_payment_closed(tran_id, Payment.Status.CANCELLED)
            else:
                payment_service.process_payment_result(tran_id, val_id)
        except Exception:
            # Fail SAFE, not closed. An unexpected error here (a DB constraint,
            # a bad row, anything) must never 500 back to SSLCommerz: the
            # gateway would retry the same poisoned notification indefinitely
            # while the customer's money sits captured and uncredited (QA C7).
            # Escalate to a human instead and answer 200 so the retry storm
            # stops — the payment stays PENDING and the reconciliation job and
            # the staff review queue both still see it.
            payment_service.flag_payment_for_review(
                tran_id, "IPN processing failed"
            )
            logger.exception(
                "IPN processing failed for %s — flagged for manual review", tran_id
            )
        return Response({"detail": "ok"})


class PaymentSuccessView(APIView):
    """Browser lands here after paying. Runs the same idempotent processing
    as the IPN (this is the path that settles payments in local dev, where
    the IPN can't reach localhost), then hands off to the frontend."""

    def post(self, request):
        payment_service.process_payment_result(
            request.data.get("tran_id"), request.data.get("val_id")
        )
        return _frontend_redirect("success", request)


class PaymentFailView(APIView):
    """Browser lands here when the gateway reports a failed attempt. The
    redirect is attacker-controllable (a plain POST with any tran_id), so it
    never closes a payment by itself — the gateway is asked what actually
    happened and only its answer changes state. The default anon throttle
    stays on: each hit costs an outbound gateway call."""

    def post(self, request):
        payment_service.close_payment_from_redirect(request.data.get("tran_id"))
        return _frontend_redirect("fail", request)


class PaymentCancelView(APIView):
    """Same trust model as PaymentFailView — presentation-first, state only
    on the gateway's confirmed answer."""

    def post(self, request):
        payment_service.close_payment_from_redirect(request.data.get("tran_id"))
        return _frontend_redirect("cancel", request)
