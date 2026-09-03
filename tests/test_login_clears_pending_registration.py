from datetime import datetime
from unittest.mock import patch

import pytest

from app.extensions import db
from app.models import PendingEmailVerification, User
from app.services.auth_service import AuthService

SYNTHETIC_PASSWORD = "senha-sintetica-issue-53-123"
PENDING_SYNTHETIC_PASSWORD = "senha-sintetica-pendente-issue-53-456"


def _create_user(email, *, verified=True):
    user = User(
        name="Usuario Issue 53",
        email=email,
        email_verified_at=datetime.utcnow() if verified else None,
    )
    user.set_password(SYNTHETIC_PASSWORD)
    db.session.add(user)
    db.session.commit()
    return user


def _start_registration(email):
    """Cria um PendingEmailVerification realista via o fluxo real do
    service (EmailService mockado, nenhum e-mail real enviado)."""
    with patch("app.services.auth_service.EmailService.send_verification_email") as mock_send:
        mock_send.return_value = None
        pending = AuthService.start_registration(
            "Usuario Pendente Issue 53", email, PENDING_SYNTHETIC_PASSWORD
        )
    return pending


def _login(client, get_csrf_token, email, password):
    token = get_csrf_token(client, "/login")
    return client.post("/login", data={
        "email": email, "password": password, "csrf_token": token,
    })


def _snapshot(pending):
    """Campos relevantes para comparar antes/depois - nunca o hash
    literal, apenas se ele mudou ou não."""
    return {
        "id": pending.id,
        "email": pending.email,
        "password_hash": pending.password_hash,
        "verification_code_hash": pending.verification_code_hash,
        "expires_at": pending.expires_at,
        "attempts": pending.attempts,
        "resend_count": pending.resend_count,
        "last_sent_at": pending.last_sent_at,
        "verified_at": pending.verified_at,
    }


# Login valido remove a chave -----------------------------------------------

class TestValidLoginClearsPendingRegistration:
    def test_removes_key_when_present(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("removida.issue53@example.com")
            pending = _start_registration("abandonado.issue53@example.com")
            pending_id = str(pending.id)

        with client.session_transaction() as sess:
            sess["pending_registration_id"] = pending_id

        resp = _login(client, get_csrf_token, "removida.issue53@example.com", SYNTHETIC_PASSWORD)
        assert resp.status_code == 302

        with client.session_transaction() as sess:
            assert "pending_registration_id" not in sess

    def test_user_is_authenticated_after_removal(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("autenticado.issue53@example.com")

        with client.session_transaction() as sess:
            sess["pending_registration_id"] = "id-sintetico-qualquer"

        _login(client, get_csrf_token, "autenticado.issue53@example.com", SYNTHETIC_PASSWORD)

        with client.session_transaction() as sess:
            assert "_user_id" in sess
            assert "pending_registration_id" not in sess

    def test_dashboard_is_accessible_after_login(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("dashboard.issue53@example.com")

        with client.session_transaction() as sess:
            sess["pending_registration_id"] = "id-sintetico-qualquer"

        _login(client, get_csrf_token, "dashboard.issue53@example.com", SYNTHETIC_PASSWORD)
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 200

    def test_valid_login_without_preexisting_key_still_works(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("semchave.issue53@example.com")

        resp = _login(client, get_csrf_token, "semchave.issue53@example.com", SYNTHETIC_PASSWORD)
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert "pending_registration_id" not in sess

    def test_pop_is_behaviorally_idempotent_across_two_logins(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("idempotente.issue53@example.com")

        with client.session_transaction() as sess:
            sess["pending_registration_id"] = "id-sintetico-qualquer"

        resp1 = _login(client, get_csrf_token, "idempotente.issue53@example.com", SYNTHETIC_PASSWORD)
        assert resp1.status_code == 302

        with client.session_transaction() as sess:
            assert "pending_registration_id" not in sess

        # Sessao ja autenticada, chave ja ausente - um segundo POST /login
        # (reautenticacao) nao pode falhar so por causa da chave ausente.
        resp2 = _login(client, get_csrf_token, "idempotente.issue53@example.com", SYNTHETIC_PASSWORD)
        assert resp2.status_code == 302


# Casos que NAO devem remover a chave ----------------------------------------

class TestKeyPreservedWhenLoginDoesNotSucceed:
    def test_invalid_credentials_preserve_key(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("credenciais.invalidas.issue53@example.com")

        with client.session_transaction() as sess:
            sess["pending_registration_id"] = "id-sintetico-qualquer"

        resp = _login(client, get_csrf_token, "credenciais.invalidas.issue53@example.com", "senha-errada")
        assert resp.status_code == 200
        with client.session_transaction() as sess:
            assert sess.get("pending_registration_id") == "id-sintetico-qualquer"

    def test_unverified_user_preserves_key_and_existing_contract(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("naoverificado.issue53@example.com", verified=False)

        with client.session_transaction() as sess:
            sess["pending_registration_id"] = "id-sintetico-qualquer"

        resp = _login(client, get_csrf_token, "naoverificado.issue53@example.com", SYNTHETIC_PASSWORD)
        html = resp.data.decode("utf-8")
        assert resp.status_code == 200
        assert "E-mail n" in html  # "E-mail não verificado."
        with client.session_transaction() as sess:
            assert sess.get("pending_registration_id") == "id-sintetico-qualquer"
            assert "_user_id" not in sess

    def test_get_login_does_not_remove_key(self, client):
        with client.session_transaction() as sess:
            sess["pending_registration_id"] = "id-sintetico-qualquer"

        resp = client.get("/login")
        assert resp.status_code == 200
        with client.session_transaction() as sess:
            assert sess.get("pending_registration_id") == "id-sintetico-qualquer"

    def test_login_user_returning_false_preserves_key_and_prior_redirect(
        self, client, app, get_csrf_token
    ):
        """Não recria a política de `is_active` (nunca definida como False
        no projeto) - apenas confirma, via monkeypatch no símbolo usado
        pela rota, que a chave só é removida quando `login_user`
        realmente aceita a sessão, e que o contrato público preexistente
        (redirect incondicional para '/' quando `user` é truthy,
        independente do retorno de `login_user`) não foi alterado por
        esta Issue."""
        with app.app_context():
            _create_user("loginuserfalse.issue53@example.com")

        with client.session_transaction() as sess:
            sess["pending_registration_id"] = "id-sintetico-qualquer"

        with patch("app.blueprints.auth.login_user", return_value=False):
            resp = _login(
                client, get_csrf_token, "loginuserfalse.issue53@example.com", SYNTHETIC_PASSWORD
            )

        assert resp.status_code == 302
        assert resp.headers.get("Location") == "/"
        with client.session_transaction() as sess:
            assert sess.get("pending_registration_id") == "id-sintetico-qualquer"
            assert "_user_id" not in sess


# Outras chaves da sessao nao sao afetadas -----------------------------------

class TestOtherSessionKeysUnaffected:
    def test_synthetic_benign_key_survives_login(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("chavebenigna.issue53@example.com")

        with client.session_transaction() as sess:
            sess["pending_registration_id"] = "id-sintetico-qualquer"
            sess["chave_benigna_sintetica"] = "valor-sintetico"

        _login(client, get_csrf_token, "chavebenigna.issue53@example.com", SYNTHETIC_PASSWORD)

        with client.session_transaction() as sess:
            assert sess.get("chave_benigna_sintetica") == "valor-sintetico"
            assert "pending_registration_id" not in sess

    def test_csrf_token_survives_login(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("csrftoken.issue53@example.com")

        with client.session_transaction() as sess:
            sess["pending_registration_id"] = "id-sintetico-qualquer"

        _login(client, get_csrf_token, "csrftoken.issue53@example.com", SYNTHETIC_PASSWORD)

        with client.session_transaction() as sess:
            assert "csrf_token" in sess


# Registro pendente no banco permanece intacto -------------------------------

class TestPendingRegistrationRecordUnaffected:
    def test_pending_record_and_all_fields_unchanged_in_db(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("registrointacto.issue53@example.com")
            pending = _start_registration("abandonado2.issue53@example.com")
            pending_id = pending.id
            before = _snapshot(pending)

        with client.session_transaction() as sess:
            sess["pending_registration_id"] = str(pending_id)

        _login(client, get_csrf_token, "registrointacto.issue53@example.com", SYNTHETIC_PASSWORD)

        with app.app_context():
            reloaded = PendingEmailVerification.query.filter_by(id=pending_id).first()
            assert reloaded is not None
            after = _snapshot(reloaded)

        assert before == after

    def test_no_email_sent_during_login(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("sememail.issue53@example.com")
            pending = _start_registration("abandonado3.issue53@example.com")
            pending_id = str(pending.id)

        with client.session_transaction() as sess:
            sess["pending_registration_id"] = pending_id

        with patch("app.services.auth_service.EmailService.send_verification_email") as mock_send:
            _login(client, get_csrf_token, "sememail.issue53@example.com", SYNTHETIC_PASSWORD)
            mock_send.assert_not_called()


# Isolamento entre clientes ---------------------------------------------------

class TestTwoClientsRemainIsolated:
    def test_two_independent_clients(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("isoladoA.issue53@example.com")

        client_b = client.__class__(client.application, client.response_wrapper)
        with client_b.session_transaction() as sess:
            sess["pending_registration_id"] = "id-sintetico-de-b"

        _login(client, get_csrf_token, "isoladoA.issue53@example.com", SYNTHETIC_PASSWORD)

        with client_b.session_transaction() as sess:
            assert sess.get("pending_registration_id") == "id-sintetico-de-b"
            assert "_user_id" not in sess


# Ciclo completo e CSRF --------------------------------------------------------

class TestFullCycleAndCsrf:
    def test_login_dashboard_logout_dashboard_cycle(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("ciclo.issue53@example.com")

        with client.session_transaction() as sess:
            sess["pending_registration_id"] = "id-sintetico-qualquer"

        _login(client, get_csrf_token, "ciclo.issue53@example.com", SYNTHETIC_PASSWORD)
        assert client.get("/", follow_redirects=False).status_code == 200

        logout_token = get_csrf_token(client, "/login")
        resp_logout = client.post("/logout", data={"csrf_token": logout_token})
        assert resp_logout.status_code == 302

        resp_dashboard_after = client.get("/", follow_redirects=False)
        assert resp_dashboard_after.status_code == 302
        assert resp_dashboard_after.headers.get("Location", "").startswith("/login")

    def test_login_without_csrf_is_rejected(self, client, app):
        with app.app_context():
            _create_user("semcsrf.issue53@example.com")

        resp = client.post("/login", data={
            "email": "semcsrf.issue53@example.com", "password": SYNTHETIC_PASSWORD,
        })
        assert resp.status_code == 400

    def test_logout_without_csrf_is_rejected(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("logoutsemcsrf.issue53@example.com")

        _login(client, get_csrf_token, "logoutsemcsrf.issue53@example.com", SYNTHETIC_PASSWORD)
        resp = client.post("/logout", data={})
        assert resp.status_code == 400
