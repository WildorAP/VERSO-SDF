from django.apps import AppConfig


class VersoIntegrationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "verso_integrations"
    verbose_name = "VERSO Integrations"

    def ready(self):
        from polaris.integrations import register_integrations

        from .sep1 import return_toml_contents

        register_integrations(toml=return_toml_contents)
