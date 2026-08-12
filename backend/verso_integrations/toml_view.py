"""
Wraps Polaris' stellar.toml view to force UTF-8 charset in Content-Type.

Polaris returns Content-Type: text/plain (no charset). Some browsers then
guess the encoding themselves and pick Windows-1252 instead of UTF-8,
corrupting any non-ASCII character in the TOML content (e.g. the em dash
in ORG_DESCRIPTION / currency descriptions shows up as "â€\x94" instead of "—").
"""

from polaris.sep1.views import generate_toml


def toml_view_utf8(request, *args, **kwargs):
    response = generate_toml(request, *args, **kwargs)
    # DRF's Response rebuilds the Content-Type header from `response.content_type`
    # when it renders (which happens after this function returns), so setting
    # only response["Content-Type"] gets silently overwritten. Both must be set.
    response.content_type = "text/plain; charset=utf-8"
    response["Content-Type"] = "text/plain; charset=utf-8"
    return response