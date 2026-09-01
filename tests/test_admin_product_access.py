import uuid
from datetime import datetime

import pytest

from app.extensions import db
from app.models import AuditLog, Organization, OrganizationProduct, Product, Role, User
from app.services.bootstrap_service import BootstrapService
from app.services.organization_service import OrganizationService

SYNTHETIC_PASSWORD = "senha-sintetica-issue-30-http-123"


def _create_user(email, *, is_internal_admin=False):
    user = User(
        name="Usuario Issue 30 HTTP",
        email=email,
        is_internal_admin=is_internal_admin,
        email_verified_at=datetime.utcnow(),
    )
    user.set_password(SYNTHETIC_PASSWORD)
    db.session.add(user)
    db.session.commit()
    return user


def _login(client, get_csrf_token, email):
    return client.post("/login", data={
        "email": email,
        "password": SYNTHETIC_PASSWORD,
        "csrf_token": get_csrf_token(client),
    })


def _create_organization(legal_name="Organizacao Issue 30 HTTP"):
    org = Organization(legal_name=legal_name)
    db.session.add(org)
    db.session.commit()
    return org


def _create_admin_and_login(client, app, get_csrf_token, email="admin.http.issue30@example.com"):
    with app.app_context():
        _create_user(email, is_internal_admin=True)
    _login(client, get_csrf_token, email)


# 1-2: listagem administrativa
class TestAdminListing:
    def test_org_details_lists_kalender_gedo_and_hunt(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id

        _create_admin_and_login(client, app, get_csrf_token)
        response = client.get(f"/admin/organizations/{org_id}")
        html = response.data.decode("utf-8")

        assert response.status_code == 200
        assert "L-Kalender" in html
        assert "L-GeDo" in html
        assert "L-Hunt" in html

    def test_internal_admin_without_membership_can_view_the_page(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id

        # O admin logado a seguir NUNCA recebe vínculo (OrganizationMember)
        # com esta organização - prova de que a tela não depende disso.
        _create_admin_and_login(client, app, get_csrf_token)
        response = client.get(f"/admin/organizations/{org_id}")

        assert response.status_code == 200


# 3-5, 6, 7-9, 10-17, 18-20: concessão/revogação/idempotência/isolamento via HTTP
class TestGrantRevokeHTTP:
    @pytest.mark.parametrize("code", ["kalender", "gedo", "hunt"])
    def test_admin_can_grant_each_canonical_product(self, client, app, get_csrf_token, code):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.post(f"/admin/organizations/{org_id}/products/{code}/grant", data={
            "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
        })

        assert response.status_code == 302
        assert response.headers["Location"].endswith(f"/admin/organizations/{org_id}")
        with app.app_context():
            product = Product.query.filter_by(code=code).first()
            org_product = OrganizationProduct.query.filter_by(organization_id=org_id, product_id=product.id).first()
            assert org_product.status == "active"

    def test_grant_creates_audit_log_with_actor(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        client.post(f"/admin/organizations/{org_id}/products/gedo/grant", data={
            "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
        })

        with app.app_context():
            log = AuditLog.query.filter_by(action="organization.product.granted").first()
            assert log is not None
            assert log.organization_id == org_id
            assert log.user_id is not None
            admin = User.query.filter_by(email="admin.http.issue30@example.com").first()
            assert log.user_id == admin.id

    def test_repeated_grant_via_http_does_not_duplicate(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        for _ in range(2):
            client.post(f"/admin/organizations/{org_id}/products/gedo/grant", data={
                "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
            })

        with app.app_context():
            assert OrganizationProduct.query.filter_by(organization_id=org_id).count() == 1
            assert AuditLog.query.filter_by(action="organization.product.granted").count() == 1

    def test_revoke_via_http_sets_inactive_and_preserves_row(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        client.post(f"/admin/organizations/{org_id}/products/gedo/grant", data={
            "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
        })
        response = client.post(f"/admin/organizations/{org_id}/products/gedo/revoke", data={
            "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
        })

        assert response.status_code == 302
        with app.app_context():
            product = Product.query.filter_by(code="gedo").first()
            org_product = OrganizationProduct.query.filter_by(organization_id=org_id, product_id=product.id).first()
            assert org_product.status == "inactive"
            assert OrganizationProduct.query.filter_by(organization_id=org_id).count() == 1

    def test_revoke_creates_audit_log(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        client.post(f"/admin/organizations/{org_id}/products/gedo/grant", data={
            "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
        })
        client.post(f"/admin/organizations/{org_id}/products/gedo/revoke", data={
            "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
        })

        with app.app_context():
            assert AuditLog.query.filter_by(action="organization.product.revoked").count() == 1

    def test_repeated_revoke_via_http_is_no_op(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        client.post(f"/admin/organizations/{org_id}/products/gedo/grant", data={
            "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
        })
        for _ in range(2):
            client.post(f"/admin/organizations/{org_id}/products/gedo/revoke", data={
                "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
            })

        with app.app_context():
            assert AuditLog.query.filter_by(action="organization.product.revoked").count() == 1

    def test_isolation_between_products_via_http(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        client.post(f"/admin/organizations/{org_id}/products/gedo/grant", data={
            "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
        })

        response = client.get(f"/admin/organizations/{org_id}")
        html = response.data.decode("utf-8")
        assert response.status_code == 200
        with app.app_context():
            kalender = Product.query.filter_by(code="kalender").first()
            assert OrganizationProduct.query.filter_by(organization_id=org_id, product_id=kalender.id).count() == 0

    def test_isolation_between_organizations_via_http(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_a_id = _create_organization("Organizacao A HTTP").id
            org_b_id = _create_organization("Organizacao B HTTP").id
        _create_admin_and_login(client, app, get_csrf_token)

        client.post(f"/admin/organizations/{org_a_id}/products/kalender/grant", data={
            "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_a_id}"),
        })

        with app.app_context():
            kalender = Product.query.filter_by(code="kalender").first()
            assert OrganizationProduct.query.filter_by(organization_id=org_b_id, product_id=kalender.id).count() == 0

    def test_pre_existing_grant_remains_intact_after_unrelated_grant(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            org_id = org.id
            kalender = Product.query.filter_by(code="kalender").first()
            pre_existing = OrganizationProduct(organization_id=org_id, product_id=kalender.id, status="active")
            db.session.add(pre_existing)
            db.session.commit()
            pre_existing_id = pre_existing.id
        _create_admin_and_login(client, app, get_csrf_token)

        client.post(f"/admin/organizations/{org_id}/products/hunt/grant", data={
            "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
        })

        with app.app_context():
            reloaded = OrganizationProduct.query.filter_by(id=pre_existing_id).first()
            assert reloaded is not None
            assert reloaded.status == "active"


# 21-23: autorização
class TestAuthorization:
    def test_regular_user_cannot_grant(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _create_user("usuario.comum.issue30@example.com")
        _login(client, get_csrf_token, "usuario.comum.issue30@example.com")

        response = client.post(f"/admin/organizations/{org_id}/products/gedo/grant", data={
            "csrf_token": get_csrf_token(client),
        })

        assert response.status_code == 302
        with app.app_context():
            assert OrganizationProduct.query.filter_by(organization_id=org_id).count() == 0

    def test_regular_member_of_the_organization_cannot_grant(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            org_id = org.id
            member = _create_user("membro.comum.issue30@example.com")
            OrganizationService.add_member(org_id, member.id, "member")
        _login(client, get_csrf_token, "membro.comum.issue30@example.com")

        response = client.post(f"/admin/organizations/{org_id}/products/gedo/grant", data={
            "csrf_token": get_csrf_token(client),
        })

        assert response.status_code == 302
        with app.app_context():
            assert OrganizationProduct.query.filter_by(organization_id=org_id).count() == 0

    def test_owner_of_the_organization_cannot_grant(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            org_id = org.id
            owner = _create_user("owner.comum.issue30@example.com")
            OrganizationService.add_member(org_id, owner.id, "owner")
        _login(client, get_csrf_token, "owner.comum.issue30@example.com")

        response = client.post(f"/admin/organizations/{org_id}/products/gedo/grant", data={
            "csrf_token": get_csrf_token(client),
        })

        assert response.status_code == 302
        with app.app_context():
            assert OrganizationProduct.query.filter_by(organization_id=org_id).count() == 0

    def test_unauthenticated_request_is_redirected(self, client, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id

        response = client.post(f"/admin/organizations/{org_id}/products/gedo/grant", data={})

        assert response.status_code in (302, 400)
        # Redireciona para login OU é barrado por CSRF antes mesmo da
        # autenticação - em ambos os casos, nada é criado.
        with app.app_context():
            assert OrganizationProduct.query.filter_by(organization_id=org_id).count() == 0


# 25-27: CSRF
class TestCSRF:
    def test_post_grant_without_token_returns_400(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.post(f"/admin/organizations/{org_id}/products/gedo/grant", data={})

        assert response.status_code == 400
        with app.app_context():
            assert OrganizationProduct.query.filter_by(organization_id=org_id).count() == 0

    def test_post_grant_with_invalid_token_returns_400(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.post(f"/admin/organizations/{org_id}/products/gedo/grant", data={
            "csrf_token": "token-forjado-sintetico-invalido",
        })

        assert response.status_code == 400

    def test_token_from_another_session_returns_400(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        other_client = app.test_client()
        with app.app_context():
            other_token = get_csrf_token(other_client)

        response = client.post(f"/admin/organizations/{org_id}/products/gedo/grant", data={
            "csrf_token": other_token,
        })

        assert response.status_code == 400


# 28-29: GET não altera estado
class TestGetDoesNotMutate:
    def test_get_grant_does_not_change_state(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.get(f"/admin/organizations/{org_id}/products/gedo/grant")

        assert response.status_code == 405
        with app.app_context():
            assert OrganizationProduct.query.filter_by(organization_id=org_id).count() == 0

    def test_get_revoke_does_not_change_state(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.get(f"/admin/organizations/{org_id}/products/gedo/revoke")

        assert response.status_code == 405


# 30-34: validação de entrada
class TestInputValidation:
    def test_invalid_uuid_in_url_returns_404(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.post("/admin/organizations/nao-e-um-uuid/products/gedo/grant", data={
            "csrf_token": get_csrf_token(client),
        })

        assert response.status_code == 404

    def test_nonexistent_organization_returns_404(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.post(f"/admin/organizations/{uuid.uuid4()}/products/gedo/grant", data={
            "csrf_token": get_csrf_token(client),
        })

        assert response.status_code == 404

    def test_nonexistent_product_code_is_rejected_without_creating_anything(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.post(f"/admin/organizations/{org_id}/products/produto-inexistente/grant", data={
            "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
        })

        assert response.status_code == 302
        with app.app_context():
            assert OrganizationProduct.query.filter_by(organization_id=org_id).count() == 0
            assert Product.query.filter_by(code="produto-inexistente").count() == 0

    def test_non_canonical_code_is_rejected_even_if_product_row_exists(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            org_id = org.id
            db.session.add(Product(code="l-kalender", name="Nao Canonico", url="https://rogue.example"))
            db.session.commit()
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.post(f"/admin/organizations/{org_id}/products/l-kalender/grant", data={
            "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
        })

        assert response.status_code == 302
        with app.app_context():
            assert OrganizationProduct.query.filter_by(organization_id=org_id).count() == 0

    def test_canonical_code_missing_from_database_is_rejected_safely(self, client, app, get_csrf_token):
        with app.app_context():
            # Bootstrap NÃO executado.
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.post(f"/admin/organizations/{org_id}/products/kalender/grant", data={
            "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
        })

        assert response.status_code == 302
        html = client.get(f"/admin/organizations/{org_id}").data.decode("utf-8")
        assert "Traceback" not in html


# 35: segurança do AuditLog
class TestAuditLogContentSafety:
    def test_audit_log_details_do_not_contain_secrets(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        client.post(f"/admin/organizations/{org_id}/products/gedo/grant", data={
            "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
        })

        with app.app_context():
            logs = AuditLog.query.filter(AuditLog.action.like("organization.product.%")).all()
            assert len(logs) == 1
            forbidden = ("senha", "password", "token", "cookie", "csrf", "api_key", "authorization")
            serialized = str(logs[0].details).lower()
            for word in forbidden:
                assert word not in serialized


# 38-39: forms renderizados
class TestRenderedForms:
    def test_forms_have_csrf_token(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        html = client.get(f"/admin/organizations/{org_id}").data.decode("utf-8")

        # 1 form "Adicionar Membro" (sempre presente, mesmo sem membros) +
        # 3 forms de produto (nenhuma concessão ainda -> todos "Conceder
        # acesso") + 1 form de logout na navbar (Issue #29).
        assert html.count('name="csrf_token"') == 1 + 3 + 1

    def test_forms_do_not_send_status_or_product_id(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        html = client.get(f"/admin/organizations/{org_id}").data.decode("utf-8")

        assert 'name="status"' not in html
        assert 'name="product_id"' not in html

    def test_grant_button_becomes_revoke_button_after_granting(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        client.post(f"/admin/organizations/{org_id}/products/gedo/grant", data={
            "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
        })

        html = client.get(f"/admin/organizations/{org_id}").data.decode("utf-8")
        assert f"/admin/organizations/{org_id}/products/gedo/revoke" in html
        assert "Revogar acesso" in html


# 40: a isenção CSRF continua restrita à API de leads.
#
# Decisão (revisão corretiva): removido o teste que inspecionava
# `CSRFProtect._exempt_views` (atributo privado da biblioteca). A garantia
# "só a API de leads é isenta, nunca as rotas de produto" já é coberta sem
# depender de detalhe interno:
# - `TestCSRF` acima prova comportalmente que POST /grant e /revoke SEM
#   token retornam 400 (ou seja, essas duas rotas específicas exigem CSRF -
#   não estão isentas);
# - `tests/test_csrf_protection.py::TestApiExemptionDoesNotLeakToHTMLRoutes`
#   (Issue #29) já prova que a isenção da API não vaza para rotas HTML em
#   geral;
# - a busca repositório-inteiro por `csrf.exempt` (repetida a cada revisão)
#   é uma fonte mais confiável que um teste de runtime para a propriedade
#   global "existe exatamente uma isenção em todo o código-fonte" - captura
#   qualquer nova ocorrência no código, independentemente de qual app de
#   teste foi instanciada ou quais módulos ela importou.
# Nenhuma dessas garantias depende de `_exempt_views`; o teste removido não
# acrescentava cobertura real, só uma segunda forma - mais frágil - de
# checar o mesmo fato já provado pelos testes comportamentais acima.


# Revisão corretiva: distinção entre erro de domínio (ProductAccessError)
# e falha inesperada (ProductAccessOperationError) na camada HTTP.
class TestErrorClassificationViaHTTP:
    def test_domain_error_shows_safe_message_without_logging_as_unexpected(self, client, app, get_csrf_token, caplog):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        with caplog.at_level("ERROR"):
            response = client.post(
                f"/admin/organizations/{org_id}/products/produto-inexistente/grant",
                data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
                follow_redirects=True,
            )

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        # Mensagem de domínio segura chega à tela, sem stack trace.
        assert "produto-inexistente" in html
        assert "Traceback" not in html
        # Erro de domínio esperado NUNCA é registrado como falha
        # inesperada (nenhum log de "Falha inesperada..." nesta chamada).
        assert not any("Falha inesperada" in record.message for record in caplog.records)

    def test_unexpected_failure_shows_generic_message_and_logs_exception(self, client, app, get_csrf_token, monkeypatch, caplog):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        def _raise_commit():
            raise RuntimeError("synthetic failure - constraint uq_org_prod_org_prod violated")

        monkeypatch.setattr(db.session, "commit", _raise_commit)

        with caplog.at_level("ERROR"):
            response = client.post(
                f"/admin/organizations/{org_id}/products/gedo/grant",
                data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
                follow_redirects=True,
            )

        assert response.status_code == 200
        html = response.data.decode("utf-8")

        # Mensagem pública genérica (a própria mensagem pré-definida de
        # ProductAccessOperationError) - nunca o texto da causa original,
        # nunca nome de constraint/SQL/stack trace.
        assert "Não foi possível processar a operação de acesso a produto" in html
        assert "synthetic failure" not in html
        assert "constraint" not in html.lower()
        assert "uq_org_prod_org_prod" not in html
        assert "Traceback" not in html

        # O logger.exception do servidor FOI chamado para esta falha
        # inesperada (sem fazer asserção frágil sobre o formato completo
        # do log - só que o evento de "falha inesperada" foi emitido).
        unexpected_logs = [r for r in caplog.records if "Falha inesperada" in r.message]
        assert len(unexpected_logs) == 1

        with app.app_context():
            assert OrganizationProduct.query.filter_by(organization_id=org_id).count() == 0
            assert AuditLog.query.filter_by(action="organization.product.granted").count() == 0

    def test_unexpected_failure_response_contains_no_credentials(self, client, app, get_csrf_token, monkeypatch):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        def _raise_commit():
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(db.session, "commit", _raise_commit)

        response = client.post(
            f"/admin/organizations/{org_id}/products/gedo/grant",
            data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
            follow_redirects=True,
        )

        html = response.data.decode("utf-8").lower()
        for forbidden in ("senha", "password", "csrf_token=", "cookie", SYNTHETIC_PASSWORD.lower()):
            assert forbidden not in html
