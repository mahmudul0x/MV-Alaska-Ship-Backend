from django.conf import settings
from django.shortcuts import redirect
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import payment_service
from .models import Booking, Payment
from .serializers import (
    BookingCreateSerializer,
    BookingPublicSerializer,
    BookingQuoteSerializer,
    PaymentInitiateSerializer,
    breakdown_as_json,
)


class BookingViewSet(
    mixins.CreateModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    queryset = Booking.objects.select_related("package", "room")
    serializer_class = BookingPublicSerializer
    lookup_field = "booking_code"
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "booking"

    def get_throttles(self):
        # The wizard fires a quote on every pax change; those previews must
        # not drain the (much stricter) booking-creation budget.
        if self.action == "quote":
            self.throttle_scope = "quote"
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
    """SSLCommerz server-to-server notification. The POSTed data is only a
    trigger — the verdict comes from the authenticated Validation API call
    inside process_payment_result(). Always answers 200 so the gateway stops
    retrying; forged/unknown notifications simply credit nothing."""

    def post(self, request):
        tran_id = request.data.get("tran_id")
        val_id = request.data.get("val_id")
        ipn_status = request.data.get("status")

        if ipn_status in ("FAILED",):
            payment_service.mark_payment_closed(tran_id, Payment.Status.FAILED)
        elif ipn_status in ("CANCELLED",):
            payment_service.mark_payment_closed(tran_id, Payment.Status.CANCELLED)
        else:
            payment_service.process_payment_result(tran_id, val_id)
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
    def post(self, request):
        payment_service.mark_payment_closed(
            request.data.get("tran_id"), Payment.Status.FAILED
        )
        return _frontend_redirect("fail", request)


class PaymentCancelView(APIView):
    def post(self, request):
        payment_service.mark_payment_closed(
            request.data.get("tran_id"), Payment.Status.CANCELLED
        )
        return _frontend_redirect("cancel", request)
