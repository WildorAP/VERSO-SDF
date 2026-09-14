from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_start, name="login"),
    path("register/", views.register, name="register"),
    path("verify-email/", views.verify_email, name="verify_email"),
    path("link-stellar/", views.link_stellar_start, name="link_stellar_start"),
    path("link-stellar/submit/", views.link_stellar_submit, name="link_stellar_submit"),
    path("logout/", views.logout_view, name="logout"),
]