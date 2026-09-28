"""Admin site hooks for reconciliation."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
import tempfile

from django.contrib import admin
from django.http import HttpResponse
from django.urls import path

from reconciliation.report import generate_report


def get_admin_urls():
    def report_view(request):
        days = int(request.GET.get("days", "14"))
        tmp = Path(tempfile.mkdtemp(prefix="d3_report_"))
        generate_report(days=days, output_dir=tmp)

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in tmp.iterdir():
                if path.is_file():
                    archive.write(path, arcname=path.name)
        buffer.seek(0)
        response = HttpResponse(buffer.getvalue(), content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="d3_report_{days}d.zip"'
        return response

    return [
        path(
            "reconciliation/report/",
            admin.site.admin_view(report_view),
            name="reconciliation_report_download",
        ),
    ]


_original_get_urls = admin.site.get_urls


def patched_get_urls():
    return get_admin_urls() + _original_get_urls()


admin.site.get_urls = patched_get_urls
