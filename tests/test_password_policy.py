from datetime import datetime
from unittest.mock import patch

import pytest
import werkzeug.security

from app.extensions import db
from app.models import User, PendingEmailVerification
from app.models.identity import (
    validate_password_strength,
    MIN_USER_PASSWORD_LENGTH,
    MAX_USER_PASSWORD_LENGTH,
)
from app.services.auth_service import AuthService

# Valores sintéticos, exclusivos desta suíte de testes.
SYNTHETIC_STRONG_PASSWORD = "synthetic-strong-password-123"
SYNTHETIC_SHORT_PASSWORD = "short1"


class TestPasswordStrengthValidation:
    def test_weak_password_is_rejected_by_validator(self):
        assert len(SYNTHETIC_SHORT_PASSWORD) < MIN_USER_PASSWORD_LENGTH
        with pytest.raises(ValueError):
            validate_password_strength(SYNTHETIC_SHORT_PASSWORD)

    def test_empty_password_is_rejected_by_validator(self):
        with pytest.raises(ValueError):
            validate_password_strength("")

    def test_strong_password_is_accepted_by_validator(self):
        validate_password_strength(SYNTHETIC_STRONG_PASSWORD)  # não deve levantar


class TestRegistrationRejectsWeakPassword:
    def test_weak_password_is_rejected_before_any_write(self, app):
        with app.app_context():
            before_pending_count = PendingEmailVerification.query.count()
            before_user_count = User.query.count()

            with pytest.raises(ValueError):
                AuthService.start_registration(
                    "Usuario Fraco", "usuario.fraco@example.com", SYNTHETIC_SHORT_PASSWORD
                )

            assert PendingEmailVerification.query.count() == before_pending_count
            assert User.query.count() == before_user_count

    def test_weak_password_error_message_never_contains_password(self, app):
        with app.app_context():
            try:
                AuthService.start_registration(
                    "Usuario Fraco", "usuario.fraco2@example.com", SYNTHETIC_SHORT_PASSWORD
                )
            except ValueError as exc:
                assert SYNTHETIC_SHORT_PASSWORD not in str(exc)


class TestPasswordNeverStoredInPlainText:
    @patch("app.services.auth_service.EmailService.send_verification_email")
    def test_pending_and_user_never_store_raw_password(self, mock_send, app):
        with app.app_context():
            mock_send.return_value = None
            pending = AuthService.start_registration(
                "Usuario Registro", "usuario.registro@example.com", SYNTHETIC_STRONG_PASSWORD
            )
            assert SYNTHETIC_STRONG_PASSWORD not in pending.password_hash

            code = mock_send.call_args[1]["code"]
            user = AuthService.verify_email(pending.id, code)
            assert SYNTHETIC_STRONG_PASSWORD not in user.password_hash
            assert user.check_password(SYNTHETIC_STRONG_PASSWORD) is True


class TestDefinitiveRegistrationUsesUserSecureMethods:
    @patch("app.services.auth_service.EmailService.send_verification_email")
    def test_registration_uses_user_hash_password(self, mock_send, app):
        with app.app_context():
            mock_send.return_value = None
            with patch(
                "app.services.auth_service.User.hash_password",
                wraps=User.hash_password,
            ) as spy_hash:
                AuthService.start_registration(
                    "Usuario Hash", "usuario.hash@example.com", SYNTHETIC_STRONG_PASSWORD
                )
                spy_hash.assert_called_once_with(SYNTHETIC_STRONG_PASSWORD)


class TestLoginUsesUserCheckPassword:
    @patch("app.services.auth_service.EmailService.send_verification_email")
    def test_authenticate_calls_user_check_password(self, mock_send, app):
        with app.app_context():
            mock_send.return_value = None
            pending = AuthService.start_registration(
                "Usuario Login", "usuario.login@example.com", SYNTHETIC_STRONG_PASSWORD
            )
            code = mock_send.call_args[1]["code"]
            AuthService.verify_email(pending.id, code)

            with patch.object(User, "check_password", wraps=User.check_password, autospec=True) as spy_check:
                result = AuthService.authenticate("usuario.login@example.com", SYNTHETIC_STRONG_PASSWORD)
                assert result is not None
                spy_check.assert_called_once()


class TestExistingHashesStillAuthenticate:
    def test_user_created_with_raw_werkzeug_hash_still_authenticates(self, app):
        with app.app_context():
            # Simula um usuário criado antes desta centralização, cujo hash
            # foi gerado diretamente via werkzeug (mesmo algoritmo usado por
            # User.hash_password/set_password) - deve continuar autenticando
            # sem exigir migração/re-hash.
            legacy_hash = werkzeug.security.generate_password_hash("senha-legada-sintetica-1")
            import uuid
            user = User(
                name="Usuario Legado",
                email="usuario.legado@example.com",
                password_hash=legacy_hash,
                email_verified_at=datetime.utcnow(),
            )
            db.session.add(user)
            db.session.commit()

            result = AuthService.authenticate("usuario.legado@example.com", "senha-legada-sintetica-1")
            assert result is not None
            assert result.id == user.id


class TestVerificationCodeIndependentFromPassword:
    @patch("app.services.auth_service.EmailService.send_verification_email")
    def test_verification_code_hash_differs_from_password_hash(self, mock_send, app):
        with app.app_context():
            mock_send.return_value = None
            pending = AuthService.start_registration(
                "Usuario Codigo", "usuario.codigo@example.com", SYNTHETIC_STRONG_PASSWORD
            )
            assert pending.verification_code_hash != pending.password_hash

    @patch("app.services.auth_service.EmailService.send_verification_email")
    def test_wrong_code_with_correct_password_hash_still_fails(self, mock_send, app):
        with app.app_context():
            mock_send.return_value = None
            pending = AuthService.start_registration(
                "Usuario CodigoErrado", "usuario.codigoerrado@example.com", SYNTHETIC_STRONG_PASSWORD
            )
            with pytest.raises(ValueError):
                AuthService.verify_email(pending.id, "000000")
            assert User.query.filter_by(email="usuario.codigoerrado@example.com").count() == 0


class TestEmailVerificationFlowStillWorks:
    @patch("app.services.auth_service.EmailService.send_verification_email")
    def test_full_register_then_verify_flow(self, mock_send, app):
        with app.app_context():
            mock_send.return_value = None
            pending = AuthService.start_registration(
                "Usuario Fluxo", "usuario.fluxo@example.com", SYNTHETIC_STRONG_PASSWORD
            )
            code = mock_send.call_args[1]["code"]

            user = AuthService.verify_email(pending.id, code)

            assert user.email == "usuario.fluxo@example.com"
            assert user.email_verified_at is not None

            refreshed_pending = PendingEmailVerification.query.get(pending.id)
            assert refreshed_pending.verified_at is not None


class TestDuplicateEmailDoesNotModifyExistingUser:
    @patch("app.services.auth_service.EmailService.send_verification_email")
    def test_duplicate_registration_does_not_touch_existing_user(self, mock_send, app):
        with app.app_context():
            mock_send.return_value = None
            pending = AuthService.start_registration(
                "Usuario Original", "usuario.duplicado@example.com", SYNTHETIC_STRONG_PASSWORD
            )
            code = mock_send.call_args[1]["code"]
            original_user = AuthService.verify_email(pending.id, code)
            original_hash = original_user.password_hash

            with pytest.raises(ValueError):
                AuthService.start_registration(
                    "Usuario Impostor", "usuario.duplicado@example.com", "outra-senha-sintetica-456"
                )

            db.session.refresh(original_user)
            assert original_user.password_hash == original_hash


class TestPersistenceFailureRollsBack:
    def test_start_registration_rolls_back_on_commit_failure(self, app, monkeypatch):
        with app.app_context():
            def _raise_commit(*args, **kwargs):
                raise RuntimeError("synthetic failure")

            monkeypatch.setattr(db.session, "commit", _raise_commit)

            with pytest.raises(ValueError):
                AuthService.start_registration(
                    "Usuario Rollback", "usuario.rollback@example.com", SYNTHETIC_STRONG_PASSWORD
                )


class TestHashPasswordCannotBeBypassed:
    def test_hash_password_rejects_weak_password_directly(self):
        # Chamando User.hash_password() diretamente, sem passar por
        # set_password() nem por validate_password_strength() antes: a
        # validação deve ocorrer dentro do próprio hash_password().
        with pytest.raises(ValueError):
            User.hash_password(SYNTHETIC_SHORT_PASSWORD)

    def test_hash_password_rejection_message_never_contains_password(self):
        try:
            User.hash_password(SYNTHETIC_SHORT_PASSWORD)
        except ValueError as exc:
            assert SYNTHETIC_SHORT_PASSWORD not in str(exc)

    def test_set_password_and_hash_password_apply_same_policy(self, app):
        with app.app_context():
            user = User(name="Politica Teste", email="politica.teste@example.com")

            with pytest.raises(ValueError):
                user.set_password(SYNTHETIC_SHORT_PASSWORD)
            with pytest.raises(ValueError):
                User.hash_password(SYNTHETIC_SHORT_PASSWORD)

            # Ambos aceitam a mesma senha forte sem levantar exceção.
            user.set_password(SYNTHETIC_STRONG_PASSWORD)
            User.hash_password(SYNTHETIC_STRONG_PASSWORD)
            assert user.check_password(SYNTHETIC_STRONG_PASSWORD) is True

    def test_set_password_still_hashes_only_via_hash_password(self, app):
        with app.app_context():
            user = User(name="Delegacao Teste", email="delegacao.teste@example.com")
            with patch.object(User, "hash_password", wraps=User.hash_password) as spy:
                user.set_password(SYNTHETIC_STRONG_PASSWORD)
                spy.assert_called_once_with(SYNTHETIC_STRONG_PASSWORD)


class TestExactPasswordHandling:
    def test_password_with_internal_spaces_is_preserved(self, app):
        with app.app_context():
            password_with_spaces = "senha com espacos internos 123"
            user = User(name="Espacos Teste", email="espacos.teste@example.com")
            user.set_password(password_with_spaces)

            assert user.check_password(password_with_spaces) is True
            assert user.check_password(password_with_spaces.replace(" ", "")) is False

    def test_unicode_password_is_preserved(self, app):
        with app.app_context():
            unicode_password = "senha-sintética-áéíóú-日本語-🔒123"
            user = User(name="Unicode Teste", email="unicode.teste@example.com")
            user.set_password(unicode_password)

            assert user.check_password(unicode_password) is True

    def test_leading_trailing_whitespace_is_significant(self, app):
        with app.app_context():
            base_password = "senha-sintetica-base-123"
            padded_password = "  " + base_password + "  "
            user = User(name="Espacos Bordas Teste", email="espacosbordas.teste@example.com")
            user.set_password(padded_password)

            # A senha com espaços nas bordas não deve autenticar sem eles,
            # e vice-versa - nenhum strip() é aplicado antes de hash/checagem.
            assert user.check_password(padded_password) is True
            assert user.check_password(base_password) is False

    def test_whitespace_only_password_is_rejected(self):
        with pytest.raises(ValueError):
            validate_password_strength("            ")

    def test_none_password_is_rejected(self):
        with pytest.raises(ValueError):
            validate_password_strength(None)

    def test_non_string_password_is_rejected(self):
        with pytest.raises(ValueError):
            validate_password_strength(12345678901234)
        with pytest.raises(ValueError):
            validate_password_strength(["senha", "em", "lista"])

    def test_password_above_maximum_length_is_rejected_without_leaking(self):
        too_long_password = "a" * (MAX_USER_PASSWORD_LENGTH + 1)
        with pytest.raises(ValueError) as exc_info:
            validate_password_strength(too_long_password)
        assert too_long_password not in str(exc_info.value)
        assert MAX_USER_PASSWORD_LENGTH >= 64

    def test_password_at_maximum_length_is_accepted(self):
        exactly_max_password = "a" * MAX_USER_PASSWORD_LENGTH
        validate_password_strength(exactly_max_password)  # não deve levantar

    def test_no_artificial_composition_policy_is_enforced(self):
        # Uma senha só com letras minúsculas, sem números/símbolos/maiúsculas,
        # mas com comprimento suficiente, deve ser aceita: não há exigência
        # de composição artificial.
        only_lowercase_password = "somenteletrasminusculas"
        assert len(only_lowercase_password) >= MIN_USER_PASSWORD_LENGTH
        validate_password_strength(only_lowercase_password)  # não deve levantar


class TestAdminCliMinimumUnaffected:
    def test_admin_cli_still_requires_twelve_characters(self):
        from app.cli import MIN_ADMIN_PASSWORD_LENGTH

        assert MIN_ADMIN_PASSWORD_LENGTH == 12


class TestEmailNormalizationConsistency:
    @patch("app.services.auth_service.EmailService.send_verification_email")
    def test_email_case_and_whitespace_are_normalized_consistently(self, mock_send, app):
        with app.app_context():
            mock_send.return_value = None
            pending = AuthService.start_registration(
                "Usuario Normalizado", "  Usuario.Normalizado@Example.com  ", SYNTHETIC_STRONG_PASSWORD
            )
            assert pending.email == "usuario.normalizado@example.com"

            code = mock_send.call_args[1]["code"]
            AuthService.verify_email(pending.id, code)

            result = AuthService.authenticate(
                "  USUARIO.NORMALIZADO@EXAMPLE.COM  ", SYNTHETIC_STRONG_PASSWORD
            )
            assert result is not None
