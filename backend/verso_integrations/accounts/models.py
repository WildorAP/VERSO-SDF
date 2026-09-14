"""
Local mirror of a VERSO-authenticated user. VERSO remains the single
source of truth for identity/passwords — this table only exists so the
Anchor can attach a Django session (request.user) to someone who
authenticated via VERSO's OAuth2 server.
"""
from django.conf import settings
from django.db import models


class AnchorProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="anchor_profile"
    )
    verso_user_id = models.PositiveIntegerField(unique=True, db_index=True)
    stellar_public_key = models.CharField(max_length=56, blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"AnchorProfile(verso_user_id={self.verso_user_id})"