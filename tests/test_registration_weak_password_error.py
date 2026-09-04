"""Issue #57: uma senha rejeitada pela política mínima (menos de 12
caracteres) fazia `AuthService.start_registration` levantar um `ValueError`
puro, que não é capturado por `except AuthError` na rota `/register` (classe
irmã, não superclasse) e caía no `except Exception` genérico, exibindo
apenas "Ocorreu um erro inesperado." ao usuário.

Estes testes cobrem, via cliente HTTP real do Flask, que a mensagem segura
e específica da política de senha agora chega ao usuário, sem nenhuma
escrita no banco e sem acionar o provider de e-mail; e, na camada de
serviço, que a conversão preserva a causa original via `raise ... from`.
"""
from unittest.mock import patch

import pytest

from app.models import AuditLog, PendingEmailVerification, User
from app.services.auth_service import AuthError, AuthService

SYNTHETIC_SHORT_PASSWORD = "curta-123"
SPECIFIC_MESSAGE = "Senha inválida: deve conter ao menos 12 caracteres."
GENERIC_MESSAGE = "Ocorreu um erro inesperado."


class TestRegisterRouteShowsSpecificPasswordError:
    def test_returns_200_with_specific_message_and_without_generic_one(
        self, client, get_csrf_token
    ):
        with patch(
            "app.services.auth_service.EmailService.send_verification_email"
        ) as mock_send:
            response = client.post("/register", data={
                "name": "Usuario Senha Curta",
                "email": "senha.curta.issue57@example.com",
                "password": SYNTHETIC_SHORT_PASSWORD,
                "password_confirm": SYNTHETIC_SHORT_PASSWORD,
                "csrf_token": get_csrf_token(client, "/register"),
            })

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert SPECIFIC_MESSAGE in html
        assert GENERIC_MESSAGE not in html
        mock_send.assert_not_called()

    def test_submitted_password_never_appears_in_response_html(
        self, client, get_csrf_token
    ):
        with patch("app.services.auth_service.EmailService.send_verification_email"):
            response = client.post("/register", data={
                "name": "Usuario Senha Curta",
                "email": "senha.curta.html.issue57@example.com",
                "password": SYNTHETIC_SHORT_PASSWORD,
                "password_confirm": SYNTHETIC_SHORT_PASSWORD,
                "csrf_token": get_csrf_token(client, "/register"),
            })

        html = response.data.decode("utf-8")
        assert SYNTHETIC_SHORT_PASSWORD not in html

    def test_submitted_password_never_appears_in_logs(
        self, client, get_csrf_token, caplog
    ):
        with caplog.at_level("DEBUG"):
            with patch("app.services.auth_service.EmailService.send_verification_email"):
                client.post("/register", data={
                    "name": "Usuario Senha Curta",
                    "email": "senha.curta.logs.issue57@example.com",
                    "password": SYNTHETIC_SHORT_PASSWORD,
                    "password_confirm": SYNTHETIC_SHORT_PASSWORD,
                    "csrf_token": get_csrf_token(client, "/register"),
                })

        for record in caplog.records:
            assert SYNTHETIC_SHORT_PASSWORD not in record.getMessage()

    def test_no_unexpected_error_is_logged(self, client, get_csrf_token, caplog):
        with caplog.at_level("ERROR"):
            with patch("app.services.auth_service.EmailService.send_verification_email"):
                client.post("/register", data={
                    "name": "Usuario Senha Curta",
                    "email": "senha.curta.errorlog.issue57@example.com",
                    "password": SYNTHETIC_SHORT_PASSWORD,
                    "password_confirm": SYNTHETIC_SHORT_PASSWORD,
                    "csrf_token": get_csrf_token(client, "/register"),
                })

        assert not any("Falha inesperada" in r.message for r in caplog.records)


class TestRegisterRouteWeakPasswordCreatesNoRecords:
    def test_no_user_or_pending_or_audit_log_created(self, client, app, get_csrf_token):
        email = "senha.curta.semregistro.issue57@example.com"

        with app.app_context():
            before_users = User.query.count()
            before_pending = PendingEmailVerification.query.count()
            before_audit = AuditLog.query.filter_by(
                action="user.registration.started"
            ).count()

        with patch("app.services.auth_service.EmailService.send_verification_email") as mock_send:
            client.post("/register", data={
                "name": "Usuario Sem Registro",
                "email": email,
                "password": SYNTHETIC_SHORT_PASSWORD,
                "password_confirm": SYNTHETIC_SHORT_PASSWORD,
                "csrf_token": get_csrf_token(client, "/register"),
            })

        with app.app_context():
            assert User.query.count() == before_users
            assert User.query.filter_by(email=email).count() == 0
            assert PendingEmailVerification.query.count() == before_pending
            assert PendingEmailVerification.query.filter_by(email=email).count() == 0
            assert (
                AuditLog.query.filter_by(action="user.registration.started").count()
                == before_audit
            )

        mock_send.assert_not_called()


class TestStartRegistrationConvertsValueErrorToAuthError:
    def test_raises_auth_error_with_original_valueerror_as_cause(self, app):
        with app.app_context():
            with pytest.raises(AuthError) as exc_info:
                AuthService.start_registration(
                    "Usuario Conversao",
                    "conversao.issue57@example.com",
                    SYNTHETIC_SHORT_PASSWORD,
                )

        assert isinstance(exc_info.value, AuthError)
        assert isinstance(exc_info.value.__cause__, ValueError)
        assert not isinstance(exc_info.value.__cause__, AuthError)
        assert str(exc_info.value) == SPECIFIC_MESSAGE
        assert str(exc_info.value.__cause__) == SPECIFIC_MESSAGE

    def test_no_write_and_no_email_when_password_is_weak(self, app):
        with app.app_context():
            before_users = User.query.count()
            before_pending = PendingEmailVerification.query.count()

            with patch(
                "app.services.auth_service.EmailService.send_verification_email"
            ) as mock_send:
                with pytest.raises(AuthError):
                    AuthService.start_registration(
                        "Usuario Sem Escrita",
                        "semescrita.issue57@example.com",
                        SYNTHETIC_SHORT_PASSWORD,
                    )
                mock_send.assert_not_called()

            assert User.query.count() == before_users
            assert PendingEmailVerification.query.count() == before_pending


class TestDirectValidationStillRaisesPlainValueError:
    """`validate_password_strength()` e `User.hash_password()` continuam
    sendo utilitários de baixo nível, sem dependência da camada de serviço -
    chamados isoladamente, continuam levantando `ValueError` puro (nunca
    `AuthError`); a reclassificação é responsabilidade exclusiva de
    `AuthService.start_registration()`, o único chamador que precisa
    apresentar a mensagem em uma rota HTTP com o contrato de erro de
    domínio."""

    def test_validate_password_strength_still_raises_plain_valueerror(self):
        from app.models.identity import validate_password_strength

        with pytest.raises(ValueError) as exc_info:
            validate_password_strength(SYNTHETIC_SHORT_PASSWORD)
        assert not isinstance(exc_info.value, AuthError)

    def test_hash_password_still_raises_plain_valueerror(self):
        with pytest.raises(ValueError) as exc_info:
            User.hash_password(SYNTHETIC_SHORT_PASSWORD)
        assert not isinstance(exc_info.value, AuthError)
