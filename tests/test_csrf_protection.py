import hmac
import uuid
from datetime import datetime
from unittest.mock import patch

import pytest

from app.extensions import db
from app.models import Organization, OrganizationMember, OrganizationProduct, Product, Role, User

SYNTHETIC_PASSWORD = "senha-sintetica-csrf-123"


def _create_user(email, *, is_internal_admin=False, password=SYNTHETIC_PASSWORD):
    user = User(
        name="Usuario CSRF",
        email=email,
        is_internal_admin=is_internal_admin,
        email_verified_at=datetime.utcnow(),
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def _login(client, get_csrf_token, email):
    return client.post("/login", data={
        "email": email,
        "password": SYNTHETIC_PASSWORD,
        "csrf_token": get_csrf_token(client),
    })


# 1-4: login exige token válido, da mesma sessão
class TestLoginRequiresValidCSRFToken:
    def test_login_with_valid_token_succeeds(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("login.valido@example.com")

        response = client.post("/login", data={
            "email": "login.valido@example.com",
            "password": SYNTHETIC_PASSWORD,
            "csrf_token": get_csrf_token(client),
        })
        assert response.status_code == 302

    def test_login_without_token_returns_400(self, client, app):
        with app.app_context():
            _create_user("login.sem.token@example.com")

        response = client.post("/login", data={
            "email": "login.sem.token@example.com",
            "password": SYNTHETIC_PASSWORD,
        })
        assert response.status_code == 400

    def test_login_with_invalid_token_returns_400(self, client, app):
        with app.app_context():
            _create_user("login.token.invalido@example.com")

        response = client.post("/login", data={
            "email": "login.token.invalido@example.com",
            "password": SYNTHETIC_PASSWORD,
            "csrf_token": "token-sintetico-forjado-invalido",
        })
        assert response.status_code == 400

    def test_token_from_another_session_returns_400(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("login.outra.sessao@example.com")

        # `with app.app_context()` aninhado: força um `flask.g` novo para
        # esta chamada. Sem isso, como o fixture `app` já mantém um
        # app_context aberto durante todo o teste, `flask_wtf.csrf.
        # generate_csrf()` reaproveitaria o `g` (e portanto o token) já
        # calculado nesta mesma execução de teste, em vez de gerar um valor
        # genuinamente novo vinculado à sessão própria de `other_client`.
        other_client = app.test_client()
        with app.app_context():
            token_from_other_session = get_csrf_token(other_client)

        response = client.post("/login", data={
            "email": "login.outra.sessao@example.com",
            "password": SYNTHETIC_PASSWORD,
            "csrf_token": token_from_other_session,
        })
        assert response.status_code == 400


# 5: registro e verificação legítimos continuam funcionando com token
class TestRegistrationAndVerificationStillWork:
    def test_register_and_verify_with_valid_tokens_succeeds(self, client, app, get_csrf_token):
        with patch("app.services.auth_service.EmailService.send_verification_email") as mock_send:
            register_response = client.post("/register", data={
                "name": "Usuario CSRF Registro",
                "email": "registro.csrf@example.com",
                "password": "Password123!",
                "password_confirm": "Password123!",
                "csrf_token": get_csrf_token(client, "/register"),
            })
            assert register_response.status_code in (200, 302)
            mock_send.assert_called_once()
            code = mock_send.call_args[1]["code"]

        verify_response = client.post("/verify", data={
            "code": code,
            "csrf_token": get_csrf_token(client, "/verify"),
        })
        assert verify_response.status_code in (200, 302)

        with app.app_context():
            user = User.query.filter_by(email="registro.csrf@example.com").first()
            assert user is not None
            assert user.email_verified_at is not None


# 6: reenvio de código exige token
class TestResendCodeRequiresToken:
    def _start_registration(self, client, get_csrf_token, email):
        with patch("app.services.auth_service.EmailService.send_verification_email"):
            client.post("/register", data={
                "name": "Usuario Reenvio CSRF",
                "email": email,
                "password": "Password123!",
                "password_confirm": "Password123!",
                "csrf_token": get_csrf_token(client, "/register"),
            })

    def test_resend_code_without_token_returns_400(self, client, get_csrf_token):
        self._start_registration(client, get_csrf_token, "reenvio.sem.token@example.com")
        response = client.post("/resend-code", data={})
        assert response.status_code == 400

    def test_resend_code_with_valid_token_is_not_blocked_by_csrf(self, client, get_csrf_token):
        self._start_registration(client, get_csrf_token, "reenvio.com.token@example.com")
        # A view sempre redireciona (302) de volta para /verify, mesmo se o
        # cooldown de negócio (VERIFICATION_RESEND_COOLDOWN) rejeitar o
        # reenvio - o que este teste prova é que a camada CSRF não bloqueia
        # a requisição quando o token é válido (nunca retorna 400 aqui).
        response = client.post("/resend-code", data={
            "csrf_token": get_csrf_token(client, "/verify"),
        })
        assert response.status_code == 302


# 7: rotas administrativas mutáveis exigem token
class TestAdminMutableRoutesRequireToken:
    def _admin_client(self, client, app, get_csrf_token, email):
        with app.app_context():
            _create_user(email, is_internal_admin=True)
        _login(client, get_csrf_token, email)
        return client

    def test_create_organization_without_token_returns_400(self, client, app, get_csrf_token):
        admin_client = self._admin_client(client, app, get_csrf_token, "admin.rotas.sem.token@example.com")
        response = admin_client.post("/admin/organizations", data={"legal_name": "Org Sem Token"})
        assert response.status_code == 400
        with app.app_context():
            assert Organization.query.filter_by(legal_name="Org Sem Token").first() is None

    def test_create_organization_with_valid_token_succeeds(self, client, app, get_csrf_token):
        admin_client = self._admin_client(client, app, get_csrf_token, "admin.rotas.com.token@example.com")
        response = admin_client.post("/admin/organizations", data={
            "legal_name": "Org Com Token",
            "csrf_token": get_csrf_token(admin_client, "/admin/organizations/new"),
        })
        assert response.status_code == 302
        with app.app_context():
            assert Organization.query.filter_by(legal_name="Org Com Token").first() is not None


# 8: token válido não substitui autenticação/autorização
class TestValidTokenDoesNotBypassAuthorization:
    def test_valid_token_without_login_does_not_authorize_admin_route(self, client, app, get_csrf_token):
        # Token obtido de uma página pública, sem nenhuma autenticação.
        token = get_csrf_token(client, "/login")

        response = client.post("/admin/organizations", data={
            "legal_name": "Org Sem Autenticacao",
            "csrf_token": token,
        })

        # O token CSRF é válido (passa a camada CSRF), mas login_required
        # ainda barra o acesso - CSRF nunca substitui autenticação.
        assert response.status_code == 302
        with app.app_context():
            assert Organization.query.filter_by(legal_name="Org Sem Autenticacao").first() is None


# 9-10: todos os formulários renderizados (incluindo em loop) contêm token
class TestAllRenderedFormsContainToken:
    def test_login_and_register_forms_contain_token(self, client):
        for url in ("/login", "/register"):
            html = client.get(url).data.decode("utf-8")
            assert 'name="csrf_token"' in html

    def test_verify_page_forms_contain_token(self, client, get_csrf_token):
        with patch("app.services.auth_service.EmailService.send_verification_email"):
            client.post("/register", data={
                "name": "Usuario Verify Forms",
                "email": "verify.forms.token@example.com",
                "password": "Password123!",
                "password_confirm": "Password123!",
                "csrf_token": get_csrf_token(client, "/register"),
            })

        html = client.get("/verify").data.decode("utf-8")
        # Form de verificação do código + form de reenvio, na mesma página.
        assert html.count('name="csrf_token"') == 2

    def test_org_form_and_org_details_forms_contain_token(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("admin.formularios.org@example.com", is_internal_admin=True)
            owner_role = Role(name="owner", description="Role owner")
            db.session.add(owner_role)
            db.session.flush()

            org = Organization(legal_name="Organizacao Formularios Loop")
            db.session.add(org)
            db.session.flush()
            org_id = org.id

            for i in range(3):
                member_user = User(
                    name=f"Membro Loop {i}",
                    email=f"membro.loop.{i}@example.com",
                    email_verified_at=datetime.utcnow(),
                )
                member_user.set_password(SYNTHETIC_PASSWORD)
                db.session.add(member_user)
                db.session.flush()
                db.session.add(OrganizationMember(
                    user_id=member_user.id, organization_id=org_id,
                    role_id=owner_role.id, status="active",
                ))
            db.session.commit()

        _login(client, get_csrf_token, "admin.formularios.org@example.com")

        # +1 em cada página autenticada: o form de logout na navbar
        # (base.html) também é protegido e conta como um csrf_token.
        new_org_html = client.get("/admin/organizations/new").data.decode("utf-8")
        assert new_org_html.count('name="csrf_token"') == 1 + 1

        org_details_html = client.get(f"/admin/organizations/{org_id}").data.decode("utf-8")
        # 3 membros x (form de papel + form de remover) + 1 form de
        # adicionar membro + 1 form de logout na navbar
        assert org_details_html.count('name="csrf_token"') == 3 * 2 + 1 + 1

    def test_users_orgs_forms_in_loop_contain_token_per_user(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("admin.usuarios.loop@example.com", is_internal_admin=True)
            for i in range(2):
                u = User(
                    name=f"Usuario Loop {i}",
                    email=f"usuario.loop.{i}@example.com",
                    email_verified_at=datetime.utcnow(),
                )
                u.set_password(SYNTHETIC_PASSWORD)
                db.session.add(u)
            db.session.commit()

        _login(client, get_csrf_token, "admin.usuarios.loop@example.com")

        html = client.get("/admin/users-orgs").data.decode("utf-8")
        # Um form de vínculo por usuário não-administrador interno (o
        # próprio admin logado é excluído do loop) + 1 form de logout na
        # navbar (base.html), presente em toda página autenticada.
        assert html.count('name="csrf_token"') == 2 + 1


# 11: GET /logout retorna 405 e mantém sessão
class TestGetLogoutIsRejected:
    def test_get_logout_returns_405_and_keeps_session(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("logout.get.protegido@example.com")
        _login(client, get_csrf_token, "logout.get.protegido@example.com")

        response = client.get("/logout")
        assert response.status_code == 405

        dashboard_response = client.get("/")
        assert dashboard_response.status_code == 200

    def test_login_page_does_not_render_logout_form(self, client):
        html = client.get("/login").data.decode("utf-8")
        assert "auth.logout" not in html
        assert "/logout" not in html


# 12-13: POST /logout exige token válido da mesma sessão
class TestPostLogoutRequiresValidToken:
    def test_post_logout_without_token_returns_400(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("logout.post.sem.token@example.com")
        _login(client, get_csrf_token, "logout.post.sem.token@example.com")

        response = client.post("/logout", data={})
        assert response.status_code == 400
        # A sessão continua autenticada - a falha CSRF não encerrou o login.
        assert client.get("/").status_code == 200

    def test_post_logout_with_invalid_token_returns_400(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("logout.post.token.invalido@example.com")
        _login(client, get_csrf_token, "logout.post.token.invalido@example.com")

        response = client.post("/logout", data={"csrf_token": "token-sintetico-forjado"})
        assert response.status_code == 400
        assert client.get("/").status_code == 200

    def test_post_logout_with_token_from_another_session_returns_400(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("logout.outra.sessao@example.com")
        _login(client, get_csrf_token, "logout.outra.sessao@example.com")

        # Ver comentário equivalente em TestLoginRequiresValidCSRFToken.
        # test_token_from_another_session_returns_400 sobre o app_context
        # aninhado.
        other_client = app.test_client()
        with app.app_context():
            other_token = get_csrf_token(other_client)

        response = client.post("/logout", data={"csrf_token": other_token})
        assert response.status_code == 400
        assert client.get("/").status_code == 200

    def test_post_logout_with_valid_token_ends_session(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("logout.valido@example.com")
        _login(client, get_csrf_token, "logout.valido@example.com")

        response = client.post("/logout", data={"csrf_token": get_csrf_token(client)})
        assert response.status_code == 302

        dashboard_response = client.get("/")
        assert dashboard_response.status_code == 302  # login_required: sessão encerrada


# 14-15: página de erro CSRF é segura
class TestCSRFErrorPage:
    def test_csrf_failure_returns_400(self, client):
        response = client.post("/login", data={"email": "x@example.com", "password": "y"})
        assert response.status_code == 400

    def test_csrf_error_page_has_no_sensitive_content(self, client):
        forged_token = "token-sintetico-que-nunca-deve-aparecer-na-resposta"
        response = client.post("/login", data={
            "email": "x@example.com",
            "password": "y",
            "csrf_token": forged_token,
        })
        html = response.data.decode("utf-8")

        assert response.status_code == 400
        assert forged_token not in html
        assert "Traceback" not in html
        assert "cookie" not in html.lower()
        assert "session" not in html.lower()

    def test_authenticated_csrf_failure_page_contains_no_token_field(self, client, app, get_csrf_token):
        # Regressão: errors/csrf.html chegou a estender base.html, que
        # contém o form de logout protegido na navbar - numa falha CSRF
        # autenticada, isso injetava um novo name="csrf_token" na própria
        # resposta de erro. O template de erro agora é standalone (não
        # estende base.html, não chama current_user/csrf_token()).
        with app.app_context():
            _create_user("admin.pagina.erro.token@example.com", is_internal_admin=True)
        _login(client, get_csrf_token, "admin.pagina.erro.token@example.com")

        response = client.post("/admin/organizations", data={
            "legal_name": "Org Pagina Erro",
            "csrf_token": "token-forjado-pagina-erro-autenticada",
        })
        html = response.data.decode("utf-8")

        assert response.status_code == 400
        assert 'name="csrf_token"' not in html


# 16-17: exceção da API de leads
class TestApiLeadsExemptFromCSRF:
    def test_leads_endpoint_works_without_csrf_token_when_api_key_valid(self, client, monkeypatch):
        monkeypatch.setenv("HUB_API_KEY", "chave-sintetica-teste-csrf-1")
        response = client.post(
            "/api/v1/leads",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "name": "Lead Sintetico CSRF",
                "email": "lead.sintetico.csrf@example.com",
            },
            headers={"Authorization": "Bearer chave-sintetica-teste-csrf-1"},
        )
        assert response.status_code == 201

    def test_leads_endpoint_rejects_valid_session_cookie_without_api_key(self, client, app, get_csrf_token, monkeypatch):
        monkeypatch.setenv("HUB_API_KEY", "chave-sintetica-teste-csrf-sessao")
        with app.app_context():
            _create_user("usuario.sessao.api@example.com")
        _login(client, get_csrf_token, "usuario.sessao.api@example.com")

        # Sessão de cookie válida (usuário autenticado no navegador), mas
        # sem o header Authorization - a API não aceita cookie como
        # credencial, então continua rejeitando.
        response = client.post(
            "/api/v1/leads",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "name": "Lead Via Cookie De Sessao",
                "email": "lead.via.cookie.sessao@example.com",
            },
        )
        assert response.status_code == 401

    def test_leads_endpoint_rejects_missing_api_key(self, client, monkeypatch):
        monkeypatch.setenv("HUB_API_KEY", "chave-sintetica-teste-csrf-2")
        response = client.post(
            "/api/v1/leads",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "name": "Lead Sem Chave",
                "email": "lead.sem.chave@example.com",
            },
        )
        assert response.status_code == 401

    def test_leads_endpoint_rejects_invalid_api_key(self, client, monkeypatch):
        monkeypatch.setenv("HUB_API_KEY", "chave-sintetica-teste-csrf-3")
        response = client.post(
            "/api/v1/leads",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "name": "Lead Chave Errada",
                "email": "lead.chave.errada@example.com",
            },
            headers={"Authorization": "Bearer chave-totalmente-errada-forjada"},
        )
        assert response.status_code == 403


# Issue #43: comparação de tempo constante (hmac.compare_digest) para
# validar a API key do endpoint de leads - substitui `token != expected_key`.
# Cobre comportamento observável (nunca duração/latência, estatisticamente
# frágil em CI) e o mecanismo real via spy em hmac.compare_digest. Valores
# usados são todos sintéticos, nunca um segredo real.
class TestApiLeadsConstantTimeKeyComparison:
    @pytest.mark.parametrize(
        "auth_header_value",
        ["Basic algum-token-basico", "BearerSemEspaco", "bearer minusculo-invalido"],
        ids=["scheme-basic", "sem-espaco", "scheme-minusculo"],
    )
    def test_malformed_authorization_header_returns_401(self, client, monkeypatch, auth_header_value):
        monkeypatch.setenv("HUB_API_KEY", "chave-sintetica-malformado-issue43")
        response = client.post(
            "/api/v1/leads",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "name": "Lead Header Malformado",
                "email": "lead.header.malformado.issue43@example.com",
            },
            headers={"Authorization": auth_header_value},
        )
        assert response.status_code == 401

    @pytest.mark.parametrize("missing_or_empty", [None, ""], ids=["ausente", "vazia"])
    def test_missing_or_empty_hub_api_key_rejects_syntactically_valid_bearer_token(
        self, client, monkeypatch, missing_or_empty
    ):
        if missing_or_empty is None:
            monkeypatch.delenv("HUB_API_KEY", raising=False)
        else:
            monkeypatch.setenv("HUB_API_KEY", missing_or_empty)

        response = client.post(
            "/api/v1/leads",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "name": "Lead Config Ausente Vazia",
                "email": "lead.config.ausente.vazia.issue43@example.com",
            },
            headers={"Authorization": "Bearer token-sintaticamente-valido-qualquer"},
        )
        assert response.status_code == 403

    @pytest.mark.parametrize("missing_or_empty", [None, ""], ids=["ausente", "vazia"])
    def test_missing_or_empty_hub_api_key_never_accepts_empty_token(
        self, client, monkeypatch, missing_or_empty
    ):
        if missing_or_empty is None:
            monkeypatch.delenv("HUB_API_KEY", raising=False)
        else:
            monkeypatch.setenv("HUB_API_KEY", missing_or_empty)

        # "Bearer " com espaço à direita e nada depois - após o split no
        # header, o token recebido pela rota é a string vazia.
        response = client.post(
            "/api/v1/leads",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "name": "Lead Token Vazio",
                "email": "lead.token.vazio.issue43@example.com",
            },
            headers={"Authorization": "Bearer "},
        )
        assert response.status_code == 403

    def test_non_ascii_token_returns_403_not_500(self, client, monkeypatch):
        monkeypatch.setenv("HUB_API_KEY", "chave-sintetica-unicode-issue43")
        response = client.post(
            "/api/v1/leads",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "name": "Lead Token Unicode",
                "email": "lead.token.unicode.issue43@example.com",
            },
            headers={"Authorization": "Bearer tôken-com-acentuação-🔒"},
        )
        assert response.status_code == 403

    def test_response_never_contains_token_or_configured_key(self, client, monkeypatch):
        monkeypatch.setenv("HUB_API_KEY", "chave-sintetica-nao-deve-vazar-issue43")
        forged_token = "token-forjado-nao-deve-aparecer-issue43"
        response = client.post(
            "/api/v1/leads",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "name": "Lead Sem Vazamento",
                "email": "lead.sem.vazamento.issue43@example.com",
            },
            headers={"Authorization": f"Bearer {forged_token}"},
        )
        body = response.data.decode("utf-8")
        assert response.status_code == 403
        assert forged_token not in body
        assert "chave-sintetica-nao-deve-vazar-issue43" not in body

    def test_compare_digest_called_with_matching_byte_arguments_when_key_configured(self, client, monkeypatch):
        synthetic_key = "chave-sintetica-mecanismo-issue43"
        real_compare_digest = hmac.compare_digest
        calls = []

        def _spy(a, b):
            calls.append((a, b))
            return real_compare_digest(a, b)

        with monkeypatch.context() as m:
            m.setenv("HUB_API_KEY", synthetic_key)
            m.setattr(hmac, "compare_digest", _spy)
            response = client.post(
                "/api/v1/leads",
                json={
                    "idempotency_key": str(uuid.uuid4()),
                    "name": "Lead Mecanismo Compare Digest",
                    "email": "lead.mecanismo.compare.digest.issue43@example.com",
                },
                headers={"Authorization": f"Bearer {synthetic_key}"},
            )

        assert response.status_code == 201
        assert len(calls) == 1
        token_arg, key_arg = calls[0]
        assert isinstance(token_arg, bytes)
        assert isinstance(key_arg, bytes)
        assert token_arg == synthetic_key.encode("utf-8")
        assert key_arg == synthetic_key.encode("utf-8")

    @pytest.mark.parametrize("missing_or_empty", [None, ""], ids=["ausente", "vazia"])
    def test_compare_digest_not_called_when_hub_api_key_missing_or_empty(
        self, client, monkeypatch, missing_or_empty
    ):
        calls = []

        def _spy(a, b):
            calls.append((a, b))
            return False

        with monkeypatch.context() as m:
            if missing_or_empty is None:
                m.delenv("HUB_API_KEY", raising=False)
            else:
                m.setenv("HUB_API_KEY", missing_or_empty)
            m.setattr(hmac, "compare_digest", _spy)
            response = client.post(
                "/api/v1/leads",
                json={
                    "idempotency_key": str(uuid.uuid4()),
                    "name": "Lead Mecanismo Sem Chave",
                    "email": "lead.mecanismo.sem.chave.issue43@example.com",
                },
                headers={"Authorization": "Bearer token-sintaticamente-valido-qualquer"},
            )

        assert response.status_code == 403
        assert calls == []


# 18: a exceção da API não se aplica a nenhuma rota HTML
class TestApiExemptionDoesNotLeakToHTMLRoutes:
    def test_admin_html_route_still_requires_token_despite_api_exemption(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("admin.exempt.check@example.com", is_internal_admin=True)
        _login(client, get_csrf_token, "admin.exempt.check@example.com")

        response = client.post("/admin/organizations", data={"legal_name": "Org Isencao Nao Deve Vazar"})
        assert response.status_code == 400
        with app.app_context():
            assert Organization.query.filter_by(legal_name="Org Isencao Nao Deve Vazar").first() is None


# 19: CSRF permanece habilitado também em TestingConfig
class TestCSRFEnabledInTestingConfig:
    def test_wtf_csrf_enabled_is_not_disabled_in_running_app(self, app):
        assert app.config.get("WTF_CSRF_ENABLED", True) is not False

    def test_testing_config_class_does_not_disable_csrf(self):
        from app.config import TestingConfig
        assert getattr(TestingConfig, "WTF_CSRF_ENABLED", True) is not False


# 20: sessões independentes não compartilham token
class TestIndependentClientsDoNotShareTokens:
    def test_two_independent_clients_get_different_tokens(self, app, get_csrf_token):
        client_a = app.test_client()
        client_b = app.test_client()

        # Cada chamada em seu próprio app_context aninhado - ver comentário
        # em TestLoginRequiresValidCSRFToken.test_token_from_another_session_returns_400.
        with app.app_context():
            token_a = get_csrf_token(client_a)
        with app.app_context():
            token_b = get_csrf_token(client_b)

        assert token_a != token_b
