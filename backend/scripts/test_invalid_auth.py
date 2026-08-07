"""Quick check: POST /auth with invalid transaction must return 400."""

import json
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.test import Client

client = Client()
payloads = [
    {"transaction": "not-a-valid-transaction"},
    {"transaction": "invalid-transaction"},
    {"transaction": 123},
    {"transaction": {"not a transaction string": True}},
    {"transaction": ""},
]

for body in payloads:
    response = client.post(
        "/auth",
        data=json.dumps(body),
        content_type="application/json",
        HTTP_HOST="localhost",
    )
    print(body, "->", response.status_code, response.content[:200])
