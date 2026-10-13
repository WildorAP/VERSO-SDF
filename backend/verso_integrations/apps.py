from django.apps import AppConfig


class VersoIntegrationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "verso_integrations"
    verbose_name = "VERSO Integrations"

    def ready(self):
        from polaris.integrations import register_integrations

        from .sep1 import return_toml_contents
        from .sep24.integration import VersoDepositIntegration
        from .sep24.withdraw_integration import VersoWithdrawIntegration
        from .sep38 import VersoQuoteIntegration
        from .rails import VersoRailsIntegration
        from .turnkey_signer import turnkey_enabled

        integrations = dict(
            toml=return_toml_contents,
            quote=VersoQuoteIntegration(),
            deposit=VersoDepositIntegration(),
            withdrawal=VersoWithdrawIntegration(),
            rails=VersoRailsIntegration(),
        )
        if turnkey_enabled():
            from .custody import VersoCustodyIntegration

            integrations["custody"] = VersoCustodyIntegration()

        register_integrations(**integrations)
