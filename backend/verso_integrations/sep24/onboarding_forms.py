from django import forms
from django.core.exceptions import ValidationError

from verso_integrations.core_client import CoreClientError, login_user, register_user, user_status, verify_email
from verso_integrations.sep24.kyc_gate import ensure_stellar_wallet_linked, get_pending_verso_user


class VersoLoginForm(forms.Form):
    email = forms.EmailField(label="Correo electrónico")
    password = forms.CharField(label="Contraseña", widget=forms.PasswordInput)

    def __init__(self, *args, request=None, transaction=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.transaction = transaction
        self.login_result = None

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        try:
            self.login_result = login_user(
                email=cleaned["email"],
                password=cleaned["password"],
            )
            if self.transaction is not None:
                ensure_stellar_wallet_linked(self.transaction, self.login_result)
        except CoreClientError as exc:
            raise ValidationError(str(exc)) from exc
        return cleaned


class VersoRegisterForm(forms.Form):
    email = forms.EmailField(label="Correo electrónico")
    password = forms.CharField(label="Contraseña", widget=forms.PasswordInput)
    nombre = forms.CharField(label="Nombres", max_length=120)
    apellidos = forms.CharField(label="Apellidos", max_length=120)

    def __init__(self, *args, request=None, transaction=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.transaction = transaction
        self.register_result = None

    def clean(self):
        cleaned = super().clean()
        if self.errors or not self.transaction:
            return cleaned
        try:
            self.register_result = register_user(
                email=cleaned["email"],
                password=cleaned["password"],
                nombre=cleaned["nombre"],
                apellidos=cleaned["apellidos"],
                stellar_public_key=self.transaction.stellar_account,
            )
        except CoreClientError as exc:
            raise ValidationError(str(exc)) from exc
        return cleaned


class VersoVerifyEmailForm(forms.Form):
    codigo = forms.CharField(
        label="Código de verificación",
        max_length=12,
        help_text="Revisa tu correo e ingresa el código enviado por VERSO.",
    )

    def __init__(self, *args, request=None, transaction=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.transaction = transaction

    def clean_codigo(self):
        codigo = self.cleaned_data["codigo"]
        if not self.request or not self.transaction:
            raise ValidationError("Vuelve a iniciar sesión o registrarte para continuar.")
        user_id, _email = get_pending_verso_user(self.request, self.transaction.id)
        if not user_id:
            raise ValidationError("Vuelve a iniciar sesión o registrarte para continuar.")
        try:
            verify_email(user_id=user_id, codigo=codigo)
        except CoreClientError as exc:
            _user_id, email = get_pending_verso_user(self.request, self.transaction.id)
            if email:
                try:
                    status = user_status(email=email)
                except CoreClientError:
                    status = None
                if status and status.email_verified:
                    return codigo
            raise ValidationError(str(exc)) from exc
        return codigo


class KycStatusForm(forms.Form):
    """Non-submitting placeholder; guidance is rendered via content_for_template."""

    acknowledge = forms.CharField(
        required=False,
        widget=forms.HiddenInput,
    )


class DiditPromptForm(forms.Form):
    """Optional submit to refresh status after returning from DIDIT."""

    refresh = forms.CharField(required=False, widget=forms.HiddenInput)
