from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    StaffBookingViewSet,
    StaffFoodMenuItemViewSet,
    StaffInvoiceViewSet,
    StaffKidPricingRuleViewSet,
    StaffLoginView,
    StaffLogoutView,
    StaffOverviewView,
    StaffPackageViewSet,
    StaffPaymentViewSet,
    StaffRoomImageViewSet,
    StaffRoomTypeViewSet,
    StaffRoomViewSet,
    StaffShipViewSet,
    StaffTokenRefreshView,
)

router = DefaultRouter()
router.register("ships", StaffShipViewSet, basename="staff-ship")
router.register("packages", StaffPackageViewSet, basename="staff-package")
router.register("bookings", StaffBookingViewSet, basename="staff-booking")
router.register("payments", StaffPaymentViewSet, basename="staff-payment")
router.register("room-types", StaffRoomTypeViewSet, basename="staff-room-type")
router.register("rooms", StaffRoomViewSet, basename="staff-room")
router.register("room-images", StaffRoomImageViewSet, basename="staff-room-image")
router.register("kid-pricing-rules", StaffKidPricingRuleViewSet, basename="staff-kid-rule")
router.register("food-menu-items", StaffFoodMenuItemViewSet, basename="staff-food-menu-item")
router.register("invoices", StaffInvoiceViewSet, basename="staff-invoice")

urlpatterns = [
    path("login/", StaffLoginView.as_view(), name="staff-login"),
    path("login/refresh/", StaffTokenRefreshView.as_view(), name="staff-token-refresh"),
    path("logout/", StaffLogoutView.as_view(), name="staff-logout"),
    path("overview/", StaffOverviewView.as_view(), name="staff-overview"),
    path("", include(router.urls)),
]
