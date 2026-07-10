from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.bookings.views import (
    BookingViewSet,
    PaymentCancelView,
    PaymentFailView,
    PaymentIPNView,
    PaymentSuccessView,
)
from apps.packages.views import CalendarView, PackageViewSet
from apps.ships.views import RoomTypeViewSet, ShipViewSet

router = DefaultRouter()
router.register("packages", PackageViewSet, basename="package")
router.register("ships", ShipViewSet, basename="ship")
router.register("room-types", RoomTypeViewSet, basename="room-type")
router.register("bookings", BookingViewSet, basename="booking")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/calendar/", CalendarView.as_view(), name="calendar"),
    path("api/payments/ipn/", PaymentIPNView.as_view(), name="payment-ipn"),
    path("api/payments/success/", PaymentSuccessView.as_view(), name="payment-success"),
    path("api/payments/fail/", PaymentFailView.as_view(), name="payment-fail"),
    path("api/payments/cancel/", PaymentCancelView.as_view(), name="payment-cancel"),
    path("api/staff/", include("apps.staff.urls")),
    path("api/", include(router.urls)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
