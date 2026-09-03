from datetime import datetime
from unittest.mock import patch

import pytest
import werkzeug.security

from app.extensions import db
from app.models import AuditLog, User
from app.services import auth_service
from app.services.auth_service import AuthService

SYNTHETIC_PASSWORD = "senha-sintetica-issue-51-123"


def _create_user(email, *, verified=True):
    user = User(
        name="Usuario Issue 51",
        email=email,
        email_verified_at=datetime.utcnow() if verified else None,
    )
    user.set_password(SYNTHETIC_PASSWORD)
    db.session.add(user)
    db.session.commit()
    return user


# Núcleo: uma verificação de hash em cada caminho -------------------------------

class TestAuthenticateAlwaysRunsOneHashCheck:
    def test_nonexistent_email_checks_dummy_hash_exactly_once(self, app):
        with app.app_context():
            with patch.object(
                werkzeug.security, "check_password_hash",
                wraps=werkzeug.security.check_password_hash,
            ) as spy_check, patch.object(
                User, "check_password", wraps=User.check_password, autospec=True
            ) as spy_real_check:
                result = AuthService.authenticate("inexistente.issue51@example.com", "qualquer-coisa")

            assert result is None
            spy_check.assert_called_once_with(auth_service._DUMMY_PASSWORD_HASH, "qualquer-coisa")
            spy_real_check.assert_not_called()

    def test_existing_user_wrong_password_checks_real_hash_exactly_once(self, app):
        with app.app_context():
            _create_user("senhaerrada.issue51@example.com")

            with patch.object(
                User, "check_password", wraps=User.check_password, autospec=True
            ) as spy_real_check:
                result = AuthService.authenticate("senhaerrada.issue51@example.com", "senha-errada-123")

            assert result is None
            spy_real_check.assert_called_once()

    def test_existing_user_correct_password_checks_real_hash_exactly_once(self, app):
        with app.app_context():
            _create_user("senhacerta.issue51@example.com")

            with patch.object(
                User, "check_password", wraps=User.check_password, autospec=True
            ) as spy_real_check:
                result = AuthService.authenticate("senhacerta.issue51@example.com", SYNTHETIC_PASSWORD)

            assert result is not None
            spy_real_check.assert_called_once()


# O caminho dummy nunca autentica -----------------------------------------------

class TestDummyPathNeverAuthenticates:
    def test_forcing_dummy_check_to_true_still_returns_none(self, app, monkeypatch):
        with app.app_context():
            with monkeypatch.context() as m:
                m.setattr(werkzeug.security, "check_password_hash", lambda *a, **kw: True)
                result = AuthService.authenticate("inexistente.forcado.issue51@example.com", "qualquer-coisa")

            assert result is None

    def test_dummy_hash_is_accepted_as_valid_hash_by_the_primitive(self, app):
        with app.app_context():
            # Não presume o valor literal correto para o dummy - apenas que
            # `check_password_hash` reconhece o formato/esquema do hash sem
            # levantar exceção, e retorna um booleano coerente para um
            # valor claramente incorreto.
            result = werkzeug.security.check_password_hash(
                auth_service._DUMMY_PASSWORD_HASH, "valor-claramente-incorreto"
            )
            assert result is False

    def test_nonexistent_email_never_creates_user_or_audit_log(self, app):
        with app.app_context():
            users_before = User.query.count()
            audit_before = AuditLog.query.count()

            AuthService.authenticate("nao.persiste.issue51@example.com", "qualquer-coisa")

            assert User.query.count() == users_before
            assert AuditLog.query.count() == audit_before


# Ciclo de vida do hash dummy ----------------------------------------------------

class TestDummyHashLifecycle:
    def test_dummy_hash_generation_does_not_run_inside_authenticate(self, app):
        with app.app_context():
            with patch.object(
                werkzeug.security, "generate_password_hash",
                wraps=werkzeug.security.generate_password_hash,
            ) as spy_generate:
                AuthService.authenticate("um.issue51@example.com", "x")
                AuthService.authenticate("dois.issue51@example.com", "y")

            spy_generate.assert_not_called()

    def test_dummy_hash_is_reused_across_calls(self, app):
        with app.app_context():
            value_before = auth_service._DUMMY_PASSWORD_HASH
            AuthService.authenticate("tres.issue51@example.com", "x")
            AuthService.authenticate("quatro.issue51@example.com", "y")
            value_after = auth_service._DUMMY_PASSWORD_HASH

            assert value_before == value_after


class TestDummyHashCompatibility:
    def test_dummy_hash_scheme_matches_real_password_hash_scheme(self, app):
        with app.app_context():
            # Compara dinamicamente com um hash gerado pela via real do
            # projeto (`User.hash_password`) no momento do teste - nunca
            # trava a versão literal dos parâmetros do Werkzeug (ex.:
            # "scrypt:32768:8:1"), apenas garante que dummy e real usam
            # exatamente o mesmo esquema/parâmetros entre si, mesmo que
            # uma atualização legítima do Werkzeug mude o default.
            real_hash = User.hash_password(SYNTHETIC_PASSWORD)
            dummy_scheme = auth_service._DUMMY_PASSWORD_HASH.split("$")[0]
            real_scheme = real_hash.split("$")[0]
            assert dummy_scheme == real_scheme


# Preservação do comportamento público -------------------------------------------

class TestAuthenticatePublicBehaviorPreserved:
    def test_nonexistent_email_returns_none(self, app):
        with app.app_context():
            assert AuthService.authenticate("naoexiste.issue51@example.com", "qualquer-coisa") is None

    def test_wrong_password_returns_none(self, app):
        with app.app_context():
            _create_user("senhaerrada2.issue51@example.com")
            assert AuthService.authenticate("senhaerrada2.issue51@example.com", "errada") is None

    def test_valid_login_returns_correct_user(self, app):
        with app.app_context():
            user = _create_user("valido.issue51@example.com")
            result = AuthService.authenticate("valido.issue51@example.com", SYNTHETIC_PASSWORD)
            assert result is not None
            assert result.id == user.id

    def test_unverified_user_with_correct_password_raises_value_error(self, app):
        with app.app_context():
            _create_user("naoverificado.issue51@example.com", verified=False)
            with pytest.raises(ValueError, match=r"^E-mail não verificado\.$"):
                AuthService.authenticate("naoverificado.issue51@example.com", SYNTHETIC_PASSWORD)

    def test_audit_log_created_only_on_successful_login(self, app):
        with app.app_context():
            _create_user("auditoria.issue51@example.com")
            audit_before = AuditLog.query.count()

            AuthService.authenticate("auditoria.issue51@example.com", "senha-errada")
            assert AuditLog.query.count() == audit_before

            AuthService.authenticate("auditoria.issue51@example.com", SYNTHETIC_PASSWORD)
            assert AuditLog.query.filter_by(action="user_login").count() == 1


# Contrato HTTP: equivalência entre e-mail inexistente e senha errada -----------

class TestLoginRouteResponseEquivalence:
    def _login(self, client, get_csrf_token, email, password):
        return client.post("/login", data={
            "email": email,
            "password": password,
            "csrf_token": get_csrf_token(client, "/login"),
        }, follow_redirects=False)

    def test_nonexistent_email_and_wrong_password_produce_equivalent_response(
        self, client, app, get_csrf_token
    ):
        with app.app_context():
            _create_user("comparacao.issue51@example.com")

        resp_nonexistent = self._login(client, get_csrf_token, "naoexiste.comparacao.issue51@example.com", "x")
        resp_wrong_password = self._login(client, get_csrf_token, "comparacao.issue51@example.com", "senha-errada")

        assert resp_nonexistent.status_code == resp_wrong_password.status_code == 200
        assert resp_nonexistent.headers.get("Location") is None
        assert resp_wrong_password.headers.get("Location") is None

        html_nonexistent = resp_nonexistent.data.decode("utf-8")
        html_wrong_password = resp_wrong_password.data.decode("utf-8")
        assert "Credenciais inválidas." in html_nonexistent
        assert "Credenciais inválidas." in html_wrong_password

    def test_dummy_hash_never_appears_in_response(self, app, client, get_csrf_token):
        resp = self._login(client, get_csrf_token, "naoexiste.semvazamento.issue51@example.com", "x")
        body_text = resp.data.decode("utf-8")
        with app.app_context():
            assert auth_service._DUMMY_PASSWORD_HASH not in body_text

    def test_valid_login_still_redirects_to_dashboard(self, app, client, get_csrf_token):
        with app.app_context():
            _create_user("redirect.issue51@example.com")

        resp = self._login(client, get_csrf_token, "redirect.issue51@example.com", SYNTHETIC_PASSWORD)
        assert resp.status_code == 302
        assert resp.headers.get("Location") == "/"


# Senha ausente/tipo inválido não pode levantar exceção -------------------------
# (revisão corretiva: a normalização a `""` para `password` não-string
# elimina a regressão em que e-mail inexistente + `password=None` passou a
# levantar `AttributeError` não tratado - o curto-circuito original nunca
# chamava a verificação nesse caso, então nunca crashava.)

class TestAuthenticateHandlesInvalidPasswordType:
    def test_none_password_nonexistent_email_returns_none_without_raising(self, app):
        with app.app_context():
            result = AuthService.authenticate("naopassword.issue51@example.com", None)
            assert result is None

    def test_none_password_existing_email_returns_none_without_raising(self, app):
        with app.app_context():
            _create_user("naopassword2.issue51@example.com")
            result = AuthService.authenticate("naopassword2.issue51@example.com", None)
            assert result is None

    def test_non_string_password_returns_none_without_raising(self, app):
        with app.app_context():
            result = AuthService.authenticate("naopasswordtipo.issue51@example.com", 12345)
            assert result is None

    def test_login_route_without_password_field_returns_200_not_500(self, client, get_csrf_token):
        resp = client.post("/login", data={
            "email": "semcampo.issue51@example.com",
            "csrf_token": get_csrf_token(client, "/login"),
        })
        assert resp.status_code == 200
        assert "Credenciais inválidas." in resp.data.decode("utf-8")

    def test_none_password_never_authenticates_even_if_check_password_is_forced_true(self, app):
        with app.app_context():
            user = _create_user("forcado.none.issue51@example.com")
            audit_before = AuditLog.query.count()

            with patch.object(
                User, "check_password", return_value=True, autospec=True
            ) as forced_check:
                result = AuthService.authenticate("forcado.none.issue51@example.com", None)

            assert result is None
            forced_check.assert_called_once()
            assert AuditLog.query.count() == audit_before

    def test_non_string_password_never_authenticates_even_if_check_password_is_forced_true(self, app):
        with app.app_context():
            _create_user("forcado.naostring.issue51@example.com")
            audit_before = AuditLog.query.count()

            with patch.object(
                User, "check_password", return_value=True, autospec=True
            ) as forced_check:
                result = AuthService.authenticate("forcado.naostring.issue51@example.com", ["nao", "eh", "string"])

            assert result is None
            forced_check.assert_called_once()
            assert AuditLog.query.count() == audit_before

    def test_existing_and_nonexistent_email_with_missing_password_produce_equivalent_response(
        self, client, app, get_csrf_token
    ):
        with app.app_context():
            _create_user("existente.semsenha.issue51@example.com")

        def _login_without_password(email):
            token = get_csrf_token(client, "/login")
            return client.post("/login", data={"email": email, "csrf_token": token})

        resp_existing = _login_without_password("existente.semsenha.issue51@example.com")
        resp_nonexistent = _login_without_password("inexistente.semsenha.issue51@example.com")

        assert resp_existing.status_code == resp_nonexistent.status_code == 200
        html_existing = resp_existing.data.decode("utf-8")
        html_nonexistent = resp_nonexistent.data.decode("utf-8")
        assert "Credenciais inválidas." in html_existing
        assert "Credenciais inválidas." in html_nonexistent


# Rate limiting continua ativo (Issue #51: cobertura ainda inexistente) --------

class TestLoginRateLimitStillEnforced:
    def test_sixth_post_within_a_minute_returns_429(self, client, get_csrf_token):
        for _ in range(5):
            resp = client.post("/login", data={
                "email": "limite.issue51@example.com",
                "password": "x",
                "csrf_token": get_csrf_token(client, "/login"),
            })
            assert resp.status_code == 200

        resp = client.post("/login", data={
            "email": "limite.issue51@example.com",
            "password": "x",
            "csrf_token": get_csrf_token(client, "/login"),
        })
        assert resp.status_code == 429
