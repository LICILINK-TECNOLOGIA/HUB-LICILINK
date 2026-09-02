import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.extensions import db
from app.models import AuditLog, PendingEmailVerification, User
from app.services.audit_service import AuditService
from app.services.auth_service import AuthError, AuthOperationError, AuthService

SYNTHETIC_PASSWORD = "senha-sintetica-issue-45-123"


def _raise(*args, **kwargs):
    raise RuntimeError("synthetic-failure-issue-45")


def _start_registration(email, name="Usuario Issue 45", password=SYNTHETIC_PASSWORD):
    with patch("app.services.auth_service.EmailService.send_verification_email") as mock_send:
        mock_send.return_value = None
        pending = AuthService.start_registration(name, email, password)
        code = mock_send.call_args[1]["code"]
    return pending, code


def _register_via_http_and_get_code(client, get_csrf_token, email, name="Usuario HTTP Issue 45"):
    with patch("app.services.auth_service.EmailService.send_verification_email") as mock_send:
        mock_send.return_value = None
        client.post("/register", data={
            "name": name,
            "email": email,
            "password": SYNTHETIC_PASSWORD,
            "password_confirm": SYNTHETIC_PASSWORD,
            "csrf_token": get_csrf_token(client, "/register"),
        })
        code = mock_send.call_args[1]["code"]
    return code


# Hierarquia de exceções ------------------------------------------------------

class TestAuthExceptionHierarchy:
    def test_both_subclass_value_error(self):
        assert issubclass(AuthError, ValueError)
        assert issubclass(AuthOperationError, ValueError)

    def test_are_sibling_classes(self):
        assert not issubclass(AuthError, AuthOperationError)
        assert not issubclass(AuthOperationError, AuthError)


# Textos de domínio preservados byte a byte -----------------------------------

class TestDomainMessagesPreservedByteForByte:
    def test_start_registration_duplicate_email(self, app):
        with app.app_context():
            # A checagem de duplicidade em start_registration é contra
            # User, não contra PendingEmailVerification (registrar de
            # novo antes de verificar apenas reutiliza a mesma pendência,
            # comportamento existente preservado) - por isso é preciso
            # verificar o e-mail primeiro para existir um User real.
            pending, code = _start_registration("duplicado.issue45@example.com")
            AuthService.verify_email(pending.id, code)
            with pytest.raises(AuthError, match=r"^E-mail já está em uso ou é inválido\.$"):
                AuthService.start_registration("Outro", "duplicado.issue45@example.com", SYNTHETIC_PASSWORD)

    def test_verify_email_pending_not_found(self, app):
        with app.app_context():
            with pytest.raises(AuthError, match=r"^Registro pendente não encontrado\.$"):
                AuthService.verify_email(str(uuid.uuid4()), "123456")

    def test_resend_code_pending_not_found(self, app):
        with app.app_context():
            with pytest.raises(AuthError, match=r"^Registro pendente não encontrado\.$"):
                AuthService.resend_code(str(uuid.uuid4()))

    def test_verify_email_already_verified(self, app):
        with app.app_context():
            pending, code = _start_registration("javerificado.issue45@example.com")
            AuthService.verify_email(pending.id, code)
            with pytest.raises(AuthError, match=r"^E-mail já verificado\.$"):
                AuthService.verify_email(pending.id, code)

    def test_resend_code_already_verified(self, app):
        with app.app_context():
            pending, code = _start_registration("reenvio.javerificado.issue45@example.com")
            AuthService.verify_email(pending.id, code)
            with pytest.raises(AuthError, match=r"^E-mail já verificado\.$"):
                AuthService.resend_code(pending.id)

    def test_verify_email_expired(self, app):
        with app.app_context():
            pending, _ = _start_registration("expirado.issue45@example.com")
            pending.expires_at = datetime.utcnow() - timedelta(seconds=1)
            db.session.commit()
            with pytest.raises(AuthError, match=r"^O código expirou\. Solicite um novo código\.$"):
                AuthService.verify_email(pending.id, "000000")

    def test_verify_email_max_attempts(self, app):
        with app.app_context():
            app.config['VERIFICATION_MAX_ATTEMPTS'] = 1
            pending, _ = _start_registration("maxtentativas.issue45@example.com")
            with pytest.raises(AuthError, match=r"^Código inválido\.$"):
                AuthService.verify_email(pending.id, "000000")
            with pytest.raises(
                AuthError, match=r"^Número máximo de tentativas atingido\. Solicite um novo código\.$"
            ):
                AuthService.verify_email(pending.id, "000000")

    def test_verify_email_wrong_code(self, app):
        with app.app_context():
            pending, _ = _start_registration("codigoerrado.issue45@example.com")
            with pytest.raises(AuthError, match=r"^Código inválido\.$"):
                AuthService.verify_email(pending.id, "000000")

    def test_resend_code_cooldown(self, app):
        with app.app_context():
            pending, _ = _start_registration("cooldown.issue45@example.com")
            with pytest.raises(AuthError, match=r"^Aguarde antes de solicitar um novo código\.$"):
                AuthService.resend_code(pending.id)

    def test_resend_code_limit(self, app):
        with app.app_context():
            app.config['VERIFICATION_MAX_RESENDS'] = 0
            app.config['VERIFICATION_RESEND_COOLDOWN'] = 0
            pending, _ = _start_registration("limitereenvio.issue45@example.com")
            with pytest.raises(AuthError, match=r"^Limite de reenvios atingido\.$"):
                AuthService.resend_code(pending.id)


# start_registration - atomicidade --------------------------------------------

class TestStartRegistrationAtomicity:
    def test_exactly_one_commit_on_success(self, app, monkeypatch):
        with app.app_context():
            commit_calls = []
            original_commit = db.session.commit

            def _counting_commit():
                commit_calls.append(1)
                return original_commit()

            with patch("app.services.auth_service.EmailService.send_verification_email"):
                with monkeypatch.context() as m:
                    m.setattr(db.session, "commit", _counting_commit)
                    AuthService.start_registration(
                        "Usuario Commit Count", "commitcount.issue45@example.com", SYNTHETIC_PASSWORD
                    )
            assert len(commit_calls) == 1

    def test_audit_failure_before_commit_rolls_back_everything(self, app, monkeypatch):
        with app.app_context():
            email = "auditfalha.registro.issue45@example.com"
            with monkeypatch.context() as m:
                m.setattr(AuditService, "log_action", _raise)
                with pytest.raises(AuthOperationError) as exc_info:
                    AuthService.start_registration("Usuario Audit Falha", email, SYNTHETIC_PASSWORD)

            assert isinstance(exc_info.value.__cause__, RuntimeError)
            assert str(exc_info.value) == "Não foi possível processar o registro. Tente novamente."
            assert PendingEmailVerification.query.filter_by(email=email).count() == 0
            assert AuditLog.query.filter_by(action='user.registration.started').count() == 0

    def test_commit_failure_rolls_back_everything(self, app, monkeypatch):
        with app.app_context():
            email = "commitfalha.registro.issue45@example.com"
            with monkeypatch.context() as m:
                m.setattr(db.session, "commit", _raise)
                with pytest.raises(AuthOperationError) as exc_info:
                    AuthService.start_registration("Usuario Commit Falha", email, SYNTHETIC_PASSWORD)

            assert isinstance(exc_info.value.__cause__, RuntimeError)
            assert str(exc_info.value) == "Não foi possível processar o registro. Tente novamente."
            assert PendingEmailVerification.query.filter_by(email=email).count() == 0

    def test_email_failure_after_commit_preserves_pending_and_audit(self, app):
        with app.app_context():
            email = "emailfalha.registro.issue45@example.com"
            with patch(
                "app.services.auth_service.EmailService.send_verification_email",
                side_effect=RuntimeError("synthetic-failure-issue-45"),
            ):
                with pytest.raises(AuthOperationError) as exc_info:
                    AuthService.start_registration("Usuario Email Falha", email, SYNTHETIC_PASSWORD)

            assert isinstance(exc_info.value.__cause__, RuntimeError)
            assert str(exc_info.value) == "Não foi possível enviar o código de confirmação. Tente novamente."

            pending = PendingEmailVerification.query.filter_by(email=email).first()
            assert pending is not None
            assert AuditLog.query.filter_by(
                action='user.registration.started', resource_id=pending.id
            ).count() == 1


# verify_email (sucesso) - atomicidade -----------------------------------------

class TestVerifyEmailSuccessAtomicity:
    def test_exactly_one_commit_on_success(self, app, monkeypatch):
        with app.app_context():
            pending, code = _start_registration("verifycommit.issue45@example.com")
            commit_calls = []
            original_commit = db.session.commit

            def _counting_commit():
                commit_calls.append(1)
                return original_commit()

            with monkeypatch.context() as m:
                m.setattr(db.session, "commit", _counting_commit)
                AuthService.verify_email(pending.id, code)
            assert len(commit_calls) == 1

    def test_audit_failure_rolls_back_user_and_pending(self, app, monkeypatch):
        with app.app_context():
            email = "verifyauditfalha.issue45@example.com"
            pending, code = _start_registration(email)
            pending_id = pending.id

            with monkeypatch.context() as m:
                m.setattr(AuditService, "log_action", _raise)
                with pytest.raises(AuthOperationError) as exc_info:
                    AuthService.verify_email(pending_id, code)

            assert isinstance(exc_info.value.__cause__, RuntimeError)
            assert str(exc_info.value) == "Erro ao criar usuário."

            db.session.expire_all()
            assert User.query.filter_by(email=email).count() == 0
            reloaded = PendingEmailVerification.query.get(pending_id)
            assert reloaded.verified_at is None
            assert AuditLog.query.filter_by(action='user.email_verified').count() == 0

    def test_commit_failure_rolls_back_user_and_pending(self, app, monkeypatch):
        with app.app_context():
            email = "verifycommitfalha.issue45@example.com"
            pending, code = _start_registration(email)
            pending_id = pending.id

            with monkeypatch.context() as m:
                m.setattr(db.session, "commit", _raise)
                with pytest.raises(AuthOperationError) as exc_info:
                    AuthService.verify_email(pending_id, code)

            assert isinstance(exc_info.value.__cause__, RuntimeError)
            db.session.expire_all()
            assert User.query.filter_by(email=email).count() == 0
            reloaded = PendingEmailVerification.query.get(pending_id)
            assert reloaded.verified_at is None


# verify_email (código inválido) - atomicidade ---------------------------------

class TestVerifyEmailInvalidCodeAtomicity:
    def test_happy_path_persists_attempt_and_audit_then_raises_domain_error(self, app):
        with app.app_context():
            pending, _ = _start_registration("tentativa.issue45@example.com")
            pending_id = pending.id
            with pytest.raises(AuthError, match=r"^Código inválido\.$"):
                AuthService.verify_email(pending_id, "000000")

            reloaded = PendingEmailVerification.query.get(pending_id)
            assert reloaded.attempts == 1
            assert AuditLog.query.filter_by(
                action='user.email_verification.failed', resource_id=pending_id
            ).count() == 1

    def test_exactly_one_commit(self, app, monkeypatch):
        with app.app_context():
            pending, _ = _start_registration("tentativacommit.issue45@example.com")
            commit_calls = []
            original_commit = db.session.commit

            def _counting_commit():
                commit_calls.append(1)
                return original_commit()

            with monkeypatch.context() as m:
                m.setattr(db.session, "commit", _counting_commit)
                with pytest.raises(AuthError):
                    AuthService.verify_email(pending.id, "000000")
            assert len(commit_calls) == 1

    def test_audit_failure_rolls_back_attempt_increment(self, app, monkeypatch):
        with app.app_context():
            pending, _ = _start_registration("tentativaauditfalha.issue45@example.com")
            pending_id = pending.id

            with monkeypatch.context() as m:
                m.setattr(AuditService, "log_action", _raise)
                with pytest.raises(AuthOperationError) as exc_info:
                    AuthService.verify_email(pending_id, "000000")

            assert isinstance(exc_info.value.__cause__, RuntimeError)
            assert str(exc_info.value) == "Não foi possível registrar a tentativa de verificação. Tente novamente."
            db.session.expire_all()
            reloaded = PendingEmailVerification.query.get(pending_id)
            assert reloaded.attempts == 0
            assert AuditLog.query.filter_by(action='user.email_verification.failed').count() == 0

    def test_commit_failure_rolls_back_attempt_increment(self, app, monkeypatch):
        with app.app_context():
            pending, _ = _start_registration("tentativacommitfalha.issue45@example.com")
            pending_id = pending.id

            with monkeypatch.context() as m:
                m.setattr(db.session, "commit", _raise)
                with pytest.raises(AuthOperationError) as exc_info:
                    AuthService.verify_email(pending_id, "000000")

            assert isinstance(exc_info.value.__cause__, RuntimeError)
            db.session.expire_all()
            reloaded = PendingEmailVerification.query.get(pending_id)
            assert reloaded.attempts == 0

    def test_domain_error_never_becomes_operation_error_when_commit_succeeds(self, app):
        with app.app_context():
            pending, _ = _start_registration("dominioseguro.issue45@example.com")
            try:
                AuthService.verify_email(pending.id, "000000")
                pytest.fail("esperava AuthError")
            except AuthOperationError:
                pytest.fail("erro de domínio foi incorretamente reembalado como AuthOperationError")
            except AuthError as exc:
                assert str(exc) == "Código inválido."


# resend_code - atomicidade ----------------------------------------------------

class TestResendCodeAtomicity:
    def test_exactly_one_commit_on_success(self, app, monkeypatch):
        with app.app_context():
            app.config['VERIFICATION_RESEND_COOLDOWN'] = 0
            pending, _ = _start_registration("reenviocommit.issue45@example.com")
            commit_calls = []
            original_commit = db.session.commit

            def _counting_commit():
                commit_calls.append(1)
                return original_commit()

            with patch("app.services.auth_service.EmailService.send_verification_email"):
                with monkeypatch.context() as m:
                    m.setattr(db.session, "commit", _counting_commit)
                    AuthService.resend_code(pending.id)
            assert len(commit_calls) == 1

    def test_audit_failure_preserves_previous_pending_state(self, app, monkeypatch):
        with app.app_context():
            app.config['VERIFICATION_RESEND_COOLDOWN'] = 0
            pending, _ = _start_registration("reenvioauditfalha.issue45@example.com")
            pending_id = pending.id
            original_hash = pending.verification_code_hash
            original_resend_count = pending.resend_count

            with monkeypatch.context() as m:
                m.setattr(AuditService, "log_action", _raise)
                with pytest.raises(AuthOperationError) as exc_info:
                    AuthService.resend_code(pending_id)

            assert isinstance(exc_info.value.__cause__, RuntimeError)
            assert str(exc_info.value) == "Não foi possível reenviar o código de confirmação. Tente novamente."
            db.session.expire_all()
            reloaded = PendingEmailVerification.query.get(pending_id)
            assert reloaded.verification_code_hash == original_hash
            assert reloaded.resend_count == original_resend_count

    def test_commit_failure_rolls_back(self, app, monkeypatch):
        with app.app_context():
            app.config['VERIFICATION_RESEND_COOLDOWN'] = 0
            pending, _ = _start_registration("reenviocommitfalha.issue45@example.com")
            pending_id = pending.id
            original_hash = pending.verification_code_hash

            with monkeypatch.context() as m:
                m.setattr(db.session, "commit", _raise)
                with pytest.raises(AuthOperationError) as exc_info:
                    AuthService.resend_code(pending_id)

            assert isinstance(exc_info.value.__cause__, RuntimeError)
            db.session.expire_all()
            reloaded = PendingEmailVerification.query.get(pending_id)
            assert reloaded.verification_code_hash == original_hash

    def test_email_failure_after_commit_preserves_new_state(self, app):
        with app.app_context():
            app.config['VERIFICATION_RESEND_COOLDOWN'] = 0
            pending, _ = _start_registration("reenvioemailfalha.issue45@example.com")
            pending_id = pending.id
            original_hash = pending.verification_code_hash

            with patch(
                "app.services.auth_service.EmailService.send_verification_email",
                side_effect=RuntimeError("synthetic-failure-issue-45"),
            ):
                with pytest.raises(AuthOperationError) as exc_info:
                    AuthService.resend_code(pending_id)

            assert isinstance(exc_info.value.__cause__, RuntimeError)
            assert str(exc_info.value) == "Não foi possível reenviar o código de confirmação. Tente novamente."
            db.session.expire_all()
            reloaded = PendingEmailVerification.query.get(pending_id)
            assert reloaded.verification_code_hash != original_hash
            assert AuditLog.query.filter_by(
                action='user.email_verification.resent', resource_id=pending_id
            ).count() == 1

    def test_happy_path_old_code_invalid_new_code_valid(self, app):
        with app.app_context():
            app.config['VERIFICATION_RESEND_COOLDOWN'] = 0
            pending, old_code = _start_registration("reenviofeliz.issue45@example.com")
            pending_id = pending.id

            with patch("app.services.auth_service.EmailService.send_verification_email") as mock_send:
                mock_send.return_value = None
                AuthService.resend_code(pending_id)
                new_code = mock_send.call_args[1]["code"]

            assert new_code != old_code

            with pytest.raises(AuthError, match=r"^Código inválido\.$"):
                AuthService.verify_email(pending_id, old_code)

            user = AuthService.verify_email(pending_id, new_code)
            assert user.email == "reenviofeliz.issue45@example.com"


# Rotas HTTP - register ---------------------------------------------------------

class TestRegisterRouteErrorHandling:
    def test_domain_error_shows_specific_message_without_unexpected_log(
        self, client, app, get_csrf_token, caplog
    ):
        with app.app_context():
            pending, code = _start_registration("rotaregistroduplicado.issue45@example.com")
            AuthService.verify_email(pending.id, code)

        with caplog.at_level("ERROR"):
            response = client.post("/register", data={
                "name": "Usuario Rota Duplicado",
                "email": "rotaregistroduplicado.issue45@example.com",
                "password": SYNTHETIC_PASSWORD,
                "password_confirm": SYNTHETIC_PASSWORD,
                "csrf_token": get_csrf_token(client, "/register"),
            }, follow_redirects=True)

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "E-mail já está em uso ou é inválido." in html
        assert not any("Falha inesperada" in r.message for r in caplog.records)

    def test_unexpected_failure_shows_generic_message_and_logs_exactly_once(
        self, client, app, get_csrf_token, monkeypatch, caplog
    ):
        with monkeypatch.context() as m:
            m.setattr(AuditService, "log_action", _raise)
            with caplog.at_level("ERROR"):
                response = client.post("/register", data={
                    "name": "Usuario Rota Falha",
                    "email": "rotaregistrofalha.issue45@example.com",
                    "password": SYNTHETIC_PASSWORD,
                    "password_confirm": SYNTHETIC_PASSWORD,
                    "csrf_token": get_csrf_token(client, "/register"),
                }, follow_redirects=True)

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "Não foi possível processar o registro. Tente novamente." in html
        assert "synthetic-failure-issue-45" not in html
        assert "Traceback" not in html
        assert SYNTHETIC_PASSWORD not in html

        unexpected_logs = [r for r in caplog.records if "Falha inesperada" in r.message]
        assert len(unexpected_logs) == 1

    def test_residual_exception_shows_generic_message_and_logs_exactly_once(
        self, client, app, get_csrf_token, monkeypatch, caplog
    ):
        # Exercita especificamente o `except Exception` residual da rota -
        # com o service já convertendo tudo internamente em AuthError/
        # AuthOperationError, o único jeito de uma exceção crua chegar até
        # a rota é injetá-la diretamente no ponto de chamada.
        with monkeypatch.context() as m:
            m.setattr(AuthService, "start_registration", _raise)
            with caplog.at_level("ERROR"):
                response = client.post("/register", data={
                    "name": "Usuario Residual",
                    "email": "residualregistro.issue45@example.com",
                    "password": SYNTHETIC_PASSWORD,
                    "password_confirm": SYNTHETIC_PASSWORD,
                    "csrf_token": get_csrf_token(client, "/register"),
                }, follow_redirects=True)

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "Ocorreu um erro inesperado." in html
        assert "synthetic-failure-issue-45" not in html

        unexpected_logs = [r for r in caplog.records if "Falha inesperada" in r.message]
        assert len(unexpected_logs) == 1


# Rotas HTTP - verify -------------------------------------------------------------

class TestVerifyRouteErrorHandling:
    def test_domain_error_shows_specific_message_without_unexpected_log(
        self, client, app, get_csrf_token, caplog
    ):
        _register_via_http_and_get_code(client, get_csrf_token, "rotaverificardominio.issue45@example.com")

        with caplog.at_level("ERROR"):
            response = client.post("/verify", data={
                "code": "000000",
                "csrf_token": get_csrf_token(client, "/verify"),
            }, follow_redirects=True)

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "Código inválido." in html
        assert not any("Falha inesperada" in r.message for r in caplog.records)

    def test_unexpected_failure_shows_generic_message_and_logs_exactly_once(
        self, client, app, get_csrf_token, monkeypatch, caplog
    ):
        code = _register_via_http_and_get_code(client, get_csrf_token, "rotaverificarfalha.issue45@example.com")

        with monkeypatch.context() as m:
            m.setattr(db.session, "commit", _raise)
            with caplog.at_level("ERROR"):
                response = client.post("/verify", data={
                    "code": code,
                    "csrf_token": get_csrf_token(client, "/verify"),
                }, follow_redirects=True)

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "Erro ao criar usuário." in html
        assert "synthetic-failure-issue-45" not in html
        assert "Traceback" not in html

        unexpected_logs = [r for r in caplog.records if "Falha inesperada" in r.message]
        assert len(unexpected_logs) == 1


# Rotas HTTP - resend-code ---------------------------------------------------------

class TestResendCodeRouteErrorHandling:
    def test_domain_error_shows_specific_message_without_unexpected_log(
        self, client, app, get_csrf_token, caplog
    ):
        app.config['VERIFICATION_RESEND_COOLDOWN'] = 999
        _register_via_http_and_get_code(client, get_csrf_token, "rotareenviardominio.issue45@example.com")

        with caplog.at_level("ERROR"):
            response = client.post("/resend-code", data={
                "csrf_token": get_csrf_token(client, "/verify"),
            }, follow_redirects=True)

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "Aguarde antes de solicitar um novo código." in html
        assert not any("Falha inesperada" in r.message for r in caplog.records)

    def test_unexpected_failure_shows_generic_message_and_logs_exactly_once(
        self, client, app, get_csrf_token, monkeypatch, caplog
    ):
        app.config['VERIFICATION_RESEND_COOLDOWN'] = 0
        _register_via_http_and_get_code(client, get_csrf_token, "rotareenviarfalha.issue45@example.com")

        with monkeypatch.context() as m:
            m.setattr(db.session, "commit", _raise)
            with caplog.at_level("ERROR"):
                response = client.post("/resend-code", data={
                    "csrf_token": get_csrf_token(client, "/verify"),
                }, follow_redirects=True)

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "Não foi possível reenviar o código de confirmação. Tente novamente." in html
        assert "synthetic-failure-issue-45" not in html
        assert "Traceback" not in html

        unexpected_logs = [r for r in caplog.records if "Falha inesperada" in r.message]
        assert len(unexpected_logs) == 1
