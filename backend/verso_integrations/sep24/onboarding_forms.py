from django import forms
from django.core.exceptions import ValidationError

from verso_integrations.core_client import (
    CoreClientError,
    login_user,
    register_user,
    update_user_profile,
    user_status,
    verify_email,
)
from verso_integrations.sep24.kyc_gate import ensure_stellar_wallet_linked, get_pending_verso_user

ORIGEN_FONDOS_CHOICES = [
    ("SUELDO", "Sueldo / salario"),
    ("AHORROS", "Ahorros"),
    ("INVERSION", "Inversión"),
    ("TRADING", "Trading / operaciones"),
    ("OTRO", "Otro"),
]


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


class VersoProfileForm(forms.Form):
    celular = forms.CharField(label="Celular", max_length=20)
    ocupacion = forms.CharField(label="Profesión o actividad comercial", max_length=100)
    origen_fondos = forms.ChoiceField(
        label="Origen de fondos",
        choices=ORIGEN_FONDOS_CHOICES,
    )
    origen_fondos_otro = forms.CharField(
        label="Especifica el origen",
        max_length=200,
        required=False,
    )
    casado = forms.ChoiceField(
        label="¿Estás casado/a?",
        choices=[("no", "No"), ("si", "Sí")],
    )
    nombre_conyugue = forms.CharField(
        label="Nombre del cónyuge",
        max_length=200,
        required=False,
    )
    es_pep = forms.ChoiceField(
        label="¿Eres o has sido PEP?",
        choices=[("no", "No"), ("si", "Sí")],
    )
    es_familiar_pep = forms.ChoiceField(
        label="¿Eres familiar de un PEP?",
        choices=[("no", "No"), ("si", "Sí")],
    )
    familiar_pep_nombre = forms.CharField(
        label="Nombre del familiar PEP",
        max_length=200,
        required=False,
    )
    familiar_pep_cargo = forms.CharField(
        label="Cargo o puesto del familiar PEP",
        max_length=200,
        required=False,
    )

    def __init__(self, *args, request=None, transaction=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.transaction = transaction
        self.profile_result = None

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned

        if cleaned.get("origen_fondos") == "OTRO" and not cleaned.get("origen_fondos_otro", "").strip():
            raise ValidationError("Especifica el origen de fondos.")

        if cleaned.get("casado") == "si" and not cleaned.get("nombre_conyugue", "").strip():
            raise ValidationError("Ingresa el nombre de tu cónyuge.")

        if cleaned.get("es_familiar_pep") == "si":
            if not cleaned.get("familiar_pep_nombre", "").strip():
                raise ValidationError("Ingresa el nombre del familiar PEP.")
            if not cleaned.get("familiar_pep_cargo", "").strip():
                raise ValidationError("Indica el cargo o puesto del familiar PEP.")

        user_id, _email = get_pending_verso_user(self.request, self.transaction.id)
        if not user_id:
            raise ValidationError("Vuelve a iniciar sesión para continuar.")

        payload = {
            "celular": cleaned["celular"],
            "ocupacion": cleaned["ocupacion"],
            "origen_fondos": cleaned["origen_fondos"],
            "origen_fondos_otro": cleaned.get("origen_fondos_otro", ""),
            "casado": cleaned["casado"] == "si",
            "nombre_conyugue": cleaned.get("nombre_conyugue", ""),
            "es_pep": cleaned["es_pep"] == "si",
            "es_familiar_pep": cleaned["es_familiar_pep"] == "si",
            "familiar_pep_nombre": cleaned.get("familiar_pep_nombre", ""),
            "familiar_pep_cargo": cleaned.get("familiar_pep_cargo", ""),
        }

        try:
            self.profile_result = update_user_profile(user_id=user_id, profile=payload)
        except CoreClientError as exc:
            raise ValidationError(str(exc)) from exc

        return cleaned


class KycStatusForm(forms.Form):
    """Non-submitting placeholder; guidance is rendered via content_for_template."""

    acknowledge = forms.CharField(
        required=False,
        widget=forms.HiddenInput,
    )


class DiditPromptForm(forms.Form):
    """Optional submit to refresh status after returning from DIDIT."""

    refresh = forms.CharField(required=False, widget=forms.HiddenInput)
