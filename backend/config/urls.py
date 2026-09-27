from django.contrib import admin
from django.urls import include, path, re_path

import polaris.urls

from verso_integrations.root import root_view
from verso_integrations.sep10 import VersoSEP10Auth
from verso_integrations.sep24.kyc_views import kyc_callback, kyc_start, onboarding_switch
from verso_integrations.sep24.onboarding_views import sep24_onboarding
from verso_integrations.toml_view import toml_view_utf8

urlpatterns = [
    path("", root_view),
    path("admin/", admin.site.urls),
    path("sep24/onboarding/", sep24_onboarding, name="sep24_onboarding"),
    path("sep24/kyc/start/", kyc_start, name="sep24_kyc_start"),
    path("sep24/kyc/callback/", kyc_callback, name="sep24_kyc_callback"),
    path("sep24/onboarding/switch/", onboarding_switch, name="sep24_onboarding_switch"),
    # Override Polaris SEP-10 before the catch-all include (invalid XDR → 400).
    re_path(r"^auth/?$", VersoSEP10Auth.as_view()),
    # Override Polaris stellar.toml before the catch-all include (force UTF-8 charset).
    re_path(r"^\.well-known/stellar\.toml/?$", toml_view_utf8),
    path("", include(polaris.urls)),
]