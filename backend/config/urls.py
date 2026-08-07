from django.contrib import admin
from django.urls import include, path, re_path

import polaris.urls

from verso_integrations.sep10 import VersoSEP10Auth

urlpatterns = [
    path("admin/", admin.site.urls),
    # Override Polaris SEP-10 before the catch-all include (invalid XDR → 400).
    re_path(r"^auth/?$", VersoSEP10Auth.as_view()),
    path("", include(polaris.urls)),
]
