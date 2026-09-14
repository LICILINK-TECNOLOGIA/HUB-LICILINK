"""Issue #68: rotas administrativas de instalação
(`POST /admin/organizations/<org_id>/products/<code>/installation`,
`.../activate`, `.../deactivate`) e a exibição correspondente em
`admin/org_details.html`. Cobre exclusivamente a camada HTTP/admin - o
contrato do validador e do service estão em
`tests/test_organization_product_installation_service.py`."""
import uuid
from datetime import datetime

import pytest

from app.extensions import db
from app.models import (
    AuditLog,
    Organization,
    OrganizationMember,
    OrganizationProduct,
    OrganizationProductInstallation,
    Product,
    User,
)
from app.services.bootstrap_service import BootstrapService
from app.services.organization_service import OrganizationService

SYNTHETIC_PASSWORD = "senha-sintetica-issue-68-http-123"
VALID_URL = "https://instalacao-issue-68-http.local/callback"


def _create_user(email, *, is_internal_admin=False):
    user = User(
        name="Usuario Issue 68 HTTP",
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


def _create_organization(legal_name="Organizacao Issue 68 HTTP"):
    org = Organization(legal_name=legal_name)
    db.session.add(org)
    db.session.commit()
    return org


def _create_admin_and_login(client, app, get_csrf_token, email="admin.http.issue68@example.com"):
    with app.app_context():
        _create_user(email, is_internal_admin=True)
    _login(client, get_csrf_token, email)


def _grant_gedo(org_id):
    """Concede acesso 'active' ao GEDO para a organização - pré-requisito
    para configurar uma instalação (o service nunca cria o
    `OrganizationProduct` implicitamente)."""
    product = Product.query.filter_by(code="gedo").first()
    org_product = OrganizationProduct(organization_id=org_id, product_id=product.id, status="active")
    db.session.add(org_product)
    db.session.commit()
    return org_product


CONFIGURE_URL = "/admin/organizations/{org_id}/products/{code}/installation"
ACTIVATE_URL = "/admin/organizations/{org_id}/products/{code}/installation/activate"
DEACTIVATE_URL = "/admin/organizations/{org_id}/products/{code}/installation/deactivate"


# Configuração (criação/atualização) ------------------------------------------

class TestConfigureInstallationRoute:
    def test_admin_creates_installation_successfully(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.post(
            CONFIGURE_URL.format(org_id=org_id, code="gedo"),
            data={"url": VALID_URL, "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
        )

        assert response.status_code == 302
        with app.app_context():
            installation = OrganizationProductInstallation.query.join(OrganizationProduct).filter(
                OrganizationProduct.organization_id == org_id
            ).first()
            assert installation is not None
            assert installation.url == VALID_URL

    def test_admin_updates_existing_installation_url(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)

        client.post(
            CONFIGURE_URL.format(org_id=org_id, code="gedo"),
            data={"url": VALID_URL, "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
        )
        new_url = "https://instalacao-issue-68-http-nova.local/callback"
        response = client.post(
            CONFIGURE_URL.format(org_id=org_id, code="gedo"),
            data={"url": new_url, "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
        )

        assert response.status_code == 302
        with app.app_context():
            installation = OrganizationProductInstallation.query.join(OrganizationProduct).filter(
                OrganizationProduct.organization_id == org_id
            ).first()
            assert installation.url == new_url

    def test_regular_user_cannot_configure(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
            _create_user("usuario.comum.issue68@example.com")
        _login(client, get_csrf_token, "usuario.comum.issue68@example.com")

        response = client.post(
            CONFIGURE_URL.format(org_id=org_id, code="gedo"),
            data={"url": VALID_URL, "csrf_token": get_csrf_token(client)},
        )

        assert response.status_code == 302
        with app.app_context():
            assert OrganizationProductInstallation.query.count() == 0

    def test_unauthenticated_request_is_rejected(self, client, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)

        response = client.post(
            CONFIGURE_URL.format(org_id=org_id, code="gedo"),
            data={"url": VALID_URL},
        )

        assert response.status_code in (302, 400)
        with app.app_context():
            assert OrganizationProductInstallation.query.count() == 0

    def test_post_without_csrf_token_returns_400(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.post(
            CONFIGURE_URL.format(org_id=org_id, code="gedo"),
            data={"url": VALID_URL},
        )

        assert response.status_code == 400
        with app.app_context():
            assert OrganizationProductInstallation.query.count() == 0

    def test_get_does_not_mutate_and_returns_405(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.get(CONFIGURE_URL.format(org_id=org_id, code="gedo"))

        assert response.status_code == 405
        with app.app_context():
            assert OrganizationProductInstallation.query.count() == 0

    def test_invalid_url_shows_safe_message_without_raw_value(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)

        malicious = "javascript:alert(document.cookie)"
        response = client.post(
            CONFIGURE_URL.format(org_id=org_id, code="gedo"),
            data={"url": malicious, "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
            follow_redirects=True,
        )

        html = response.data.decode("utf-8")
        assert response.status_code == 200
        assert malicious not in html
        assert "Traceback" not in html
        with app.app_context():
            assert OrganizationProductInstallation.query.count() == 0

    def test_missing_organization_product_shows_safe_message(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            # Nenhum grant feito - OrganizationProduct não existe.
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.post(
            CONFIGURE_URL.format(org_id=org_id, code="gedo"),
            data={"url": VALID_URL, "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert "Traceback" not in response.data.decode("utf-8")
        with app.app_context():
            assert OrganizationProductInstallation.query.count() == 0

    def test_nonexistent_organization_returns_404(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.post(
            CONFIGURE_URL.format(org_id=uuid.uuid4(), code="gedo"),
            data={"url": VALID_URL, "csrf_token": get_csrf_token(client)},
        )

        assert response.status_code == 404

    def test_allowed_even_with_inactive_subscription(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            product = Product.query.filter_by(code="gedo").first()
            db.session.add(OrganizationProduct(organization_id=org_id, product_id=product.id, status="inactive"))
            db.session.commit()
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.post(
            CONFIGURE_URL.format(org_id=org_id, code="gedo"),
            data={"url": VALID_URL, "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
        )

        assert response.status_code == 302
        with app.app_context():
            assert OrganizationProductInstallation.query.count() == 1

    def test_exactly_one_audit_event_on_success(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)

        client.post(
            CONFIGURE_URL.format(org_id=org_id, code="gedo"),
            data={"url": VALID_URL, "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
        )

        with app.app_context():
            logs = AuditLog.query.filter_by(
                action="organization_product_installation.created"
            ).all()
            assert len(logs) == 1


# Ativação/desativação ---------------------------------------------------------

class TestActivateDeactivateInstallationRoute:
    def _configure(self, client, app, get_csrf_token, org_id):
        client.post(
            CONFIGURE_URL.format(org_id=org_id, code="gedo"),
            data={"url": VALID_URL, "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
        )

    def _activate(self, client, app, get_csrf_token, org_id):
        return client.post(
            ACTIVATE_URL.format(org_id=org_id, code="gedo"),
            data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
        )

    def test_admin_activates_then_deactivates_then_reactivates(self, client, app, get_csrf_token):
        """Issue #68 (correção pós-revisão): a instalação nasce inativa -
        o primeiro passo real do ciclo é ATIVAR (nunca desativar algo
        que já nasce inativo, o que seria um no-op de erro, não uma
        transição genuína)."""
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)
        self._configure(client, app, get_csrf_token, org_id)
        with app.app_context():
            fresh = OrganizationProductInstallation.query.join(OrganizationProduct).filter(
                OrganizationProduct.organization_id == org_id
            ).first()
            assert fresh.is_active is False

        activate_response = self._activate(client, app, get_csrf_token, org_id)
        assert activate_response.status_code == 302
        with app.app_context():
            installation = OrganizationProductInstallation.query.join(OrganizationProduct).filter(
                OrganizationProduct.organization_id == org_id
            ).first()
            assert installation.is_active is True

        deactivate_response = client.post(
            DEACTIVATE_URL.format(org_id=org_id, code="gedo"),
            data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
        )
        assert deactivate_response.status_code == 302
        with app.app_context():
            installation = OrganizationProductInstallation.query.join(OrganizationProduct).filter(
                OrganizationProduct.organization_id == org_id
            ).first()
            assert installation.is_active is False

        reactivate_response = self._activate(client, app, get_csrf_token, org_id)
        assert reactivate_response.status_code == 302
        with app.app_context():
            installation = OrganizationProductInstallation.query.join(OrganizationProduct).filter(
                OrganizationProduct.organization_id == org_id
            ).first()
            assert installation.is_active is True

    def test_repeated_activate_shows_safe_message_without_500(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)
        self._configure(client, app, get_csrf_token, org_id)
        # Primeira ativação: sucesso genuíno (a instalação nasce
        # inativa) - necessária para que a SEGUNDA tentativa abaixo seja
        # de fato uma repetição, não a primeira ativação disfarçada.
        first_activate = self._activate(client, app, get_csrf_token, org_id)
        assert first_activate.status_code == 302

        response = client.post(
            ACTIVATE_URL.format(org_id=org_id, code="gedo"),
            data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert "Traceback" not in response.data.decode("utf-8")

    def test_get_on_activate_returns_405(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)
        self._configure(client, app, get_csrf_token, org_id)

        response = client.get(ACTIVATE_URL.format(org_id=org_id, code="gedo"))
        assert response.status_code == 405

    def test_get_on_deactivate_returns_405(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)
        self._configure(client, app, get_csrf_token, org_id)

        response = client.get(DEACTIVATE_URL.format(org_id=org_id, code="gedo"))
        assert response.status_code == 405

    def test_deactivate_without_csrf_returns_400(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)
        self._configure(client, app, get_csrf_token, org_id)

        response = client.post(DEACTIVATE_URL.format(org_id=org_id, code="gedo"), data={})
        assert response.status_code == 400


# Página de detalhes da organização --------------------------------------------

class TestOrgDetailsPageInstallationDisplay:
    def test_shows_not_configured_text_when_no_installation(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)

        html = client.get(f"/admin/organizations/{org_id}").data.decode("utf-8")

        assert "Instalação não configurada" in html

    def test_shows_inativa_text_and_ativar_action_right_after_creation(self, client, app, get_csrf_token):
        """Issue #68 (correção pós-revisão): logo após criar (PRG), a
        instalação já nasce inativa - a página deve exibir "Inativa" e a
        ação disponível é "Ativar" (não "Desativar"), nunca "Ativa" por
        padrão."""
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)
        response = client.post(
            CONFIGURE_URL.format(org_id=org_id, code="gedo"),
            data={"url": VALID_URL, "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
            follow_redirects=True,
        )

        html = response.data.decode("utf-8")

        assert response.status_code == 200
        assert ">Inativa<" in html
        assert ">Ativa<" not in html
        assert VALID_URL in html
        assert ">Ativar<" in html
        assert ">Desativar<" not in html
        with app.app_context():
            installation = OrganizationProductInstallation.query.join(OrganizationProduct).filter(
                OrganizationProduct.organization_id == org_id
            ).first()
            assert installation.is_active is False

    def test_shows_ativa_text_after_explicit_activation(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)
        client.post(
            CONFIGURE_URL.format(org_id=org_id, code="gedo"),
            data={"url": VALID_URL, "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
        )
        client.post(
            ACTIVATE_URL.format(org_id=org_id, code="gedo"),
            data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
        )

        html = client.get(f"/admin/organizations/{org_id}").data.decode("utf-8")

        assert ">Ativa<" in html
        assert ">Inativa<" not in html
        assert VALID_URL in html
        assert ">Desativar<" in html

    def test_shows_inativa_text_when_installation_explicitly_deactivated(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)
        client.post(
            CONFIGURE_URL.format(org_id=org_id, code="gedo"),
            data={"url": VALID_URL, "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
        )
        client.post(
            ACTIVATE_URL.format(org_id=org_id, code="gedo"),
            data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
        )
        client.post(
            DEACTIVATE_URL.format(org_id=org_id, code="gedo"),
            data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
        )

        html = client.get(f"/admin/organizations/{org_id}").data.decode("utf-8")

        assert ">Inativa<" in html

    def test_url_is_escaped_and_never_a_clickable_link(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)
        client.post(
            CONFIGURE_URL.format(org_id=org_id, code="gedo"),
            data={"url": VALID_URL, "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
        )

        html = client.get(f"/admin/organizations/{org_id}").data.decode("utf-8")

        assert f'href="{VALID_URL}"' not in html
        assert f'<a href="{VALID_URL}"' not in html

    def test_url_input_has_maxlength_255(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)

        html = client.get(f"/admin/organizations/{org_id}").data.decode("utf-8")

        assert 'name="url"' in html
        assert 'maxlength="255"' in html

    def test_installation_forms_contain_csrf_token(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)

        html = client.get(f"/admin/organizations/{org_id}").data.decode("utf-8")

        assert f'action="/admin/organizations/{org_id}/products/gedo/installation"' in html

    def test_get_details_page_is_read_only(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)

        before_audit = None
        with app.app_context():
            before_audit = AuditLog.query.count()

        for _ in range(3):
            client.get(f"/admin/organizations/{org_id}")

        with app.app_context():
            assert AuditLog.query.count() == before_audit
            assert OrganizationProductInstallation.query.count() == 0

    def test_no_duplicate_audit_in_blueprint(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)

        client.post(
            CONFIGURE_URL.format(org_id=org_id, code="gedo"),
            data={"url": VALID_URL, "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
        )

        with app.app_context():
            assert AuditLog.query.filter(
                AuditLog.action.like("organization_product_installation.%")
            ).count() == 1

    def test_member_and_product_data_unaffected(self, client, app, get_csrf_token):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            org_id = org.id
            member = _create_user("membro.issue68.http@example.com")
            OrganizationService.add_member(org_id, member.id, "member")
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)

        html = client.get(f"/admin/organizations/{org_id}").data.decode("utf-8")

        assert "membro.issue68.http@example.com" in html
        assert "Conceder acesso" in html or "Revogar acesso" in html

    def test_no_mutation_via_tampered_hidden_parameters(self, client, app, get_csrf_token):
        """Não deve ser possível mutar uma instalação de OUTRA organização
        forjando `org_id`/`product_code` fora do que a própria rota já
        resolve a partir do caminho da URL - não existe campo oculto de
        organização/produto no formulário (`test_forms_do_not_send_status_or_product_id`
        já cobre o caso análogo de produto/status)."""
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
            _grant_gedo(org_id)
        _create_admin_and_login(client, app, get_csrf_token)

        html = client.get(f"/admin/organizations/{org_id}").data.decode("utf-8")
        assert 'name="organization_id"' not in html
        assert 'name="product_code"' not in html
