"""Issue #61: o badge de status exibido no cabeçalho da página
administrativa de detalhes da organização (`admin/org_details.html`)
estava fixado como "ATIVA", ignorando o valor real de `Organization.
is_active` - uma organização com `is_active=False` aparecia visualmente
como ativa para o administrador.

Estes testes cobrem exclusivamente a renderização desse badge (texto e
identificação visual) para os dois estados de `is_active`, e confirmam
que a rota `GET /admin/organizations/<org_id>` é somente leitura - não
duplicam nenhuma regra de negócio já coberta em `tests/
test_admin_error_handling.py` ou nos demais testes administrativos."""
import re
from datetime import datetime

from app.extensions import db
from app.models import AuditLog, Organization, OrganizationMember, User

SYNTHETIC_PASSWORD = "senha-sintetica-issue-61-123"

# Casa exclusivamente o badge de status da ORGANIZAÇÃO no cabeçalho da
# página - a classe `org-status-badge` (introduzida por esta correção,
# estritamente apresentacional) o distingue dos demais elementos com a
# classe `role-badge` genérica presentes na mesma página (papel do
# membro, badges "Com acesso"/"Sem acesso" de produto - estes últimos
# reutilizam inclusive as mesmas cores #E8F5E9/#FFEBEE, o que tornaria
# uma checagem por cor/classe genérica ambígua).
_ORG_STATUS_BADGE_RE = re.compile(
    r'<span class="role-badge org-status-badge" style="([^"]*)">\s*(ATIVA|INATIVA)\s*</span>'
)


def _create_user(email, *, name="Usuario Issue 61", is_internal_admin=False):
    user = User(
        name=name,
        email=email,
        is_internal_admin=is_internal_admin,
        email_verified_at=datetime.utcnow(),
    )
    user.set_password(SYNTHETIC_PASSWORD)
    db.session.add(user)
    db.session.commit()
    return user


def _create_organization(legal_name="Organizacao Issue 61", *, is_active=True):
    org = Organization(legal_name=legal_name, is_active=is_active)
    db.session.add(org)
    db.session.commit()
    return org


def _login(client, get_csrf_token, email):
    return client.post("/login", data={
        "email": email,
        "password": SYNTHETIC_PASSWORD,
        "csrf_token": get_csrf_token(client),
    })


def _create_admin_and_login(client, app, get_csrf_token, email="admin.issue61@example.com"):
    with app.app_context():
        _create_user(email, name="Admin Issue 61", is_internal_admin=True)
    _login(client, get_csrf_token, email)


class TestActiveOrganizationShowsAtivaBadge:
    def test_active_org_details_returns_200_with_ativa_badge(self, client, app, get_csrf_token):
        with app.app_context():
            org = _create_organization("Organizacao Issue 61 Ativa", is_active=True)
            org_id = org.id

        _create_admin_and_login(client, app, get_csrf_token)
        response = client.get(f"/admin/organizations/{org_id}")
        assert response.status_code == 200
        html = response.data.decode("utf-8")

        match = _ORG_STATUS_BADGE_RE.search(html)
        assert match is not None, "esperado o badge de status da organização no cabeçalho"
        style, text = match.group(1), match.group(2)

        assert text == "ATIVA"
        assert "#E8F5E9" in style
        assert "#2E7D32" in style

        # Não deve haver nenhuma ocorrência de "INATIVA" na página quando
        # a organização está ativa - nenhuma outra funcionalidade do HUB
        # usa esse texto hoje.
        assert "INATIVA" not in html


class TestInactiveOrganizationShowsInativaBadge:
    def test_inactive_org_details_returns_200_with_inativa_badge(self, client, app, get_csrf_token):
        with app.app_context():
            org = _create_organization("Organizacao Issue 61 Inativa", is_active=False)
            org_id = org.id

        _create_admin_and_login(client, app, get_csrf_token)
        response = client.get(f"/admin/organizations/{org_id}")
        assert response.status_code == 200
        html = response.data.decode("utf-8")

        match = _ORG_STATUS_BADGE_RE.search(html)
        assert match is not None, "esperado o badge de status da organização no cabeçalho"
        style, text = match.group(1), match.group(2)

        assert text == "INATIVA"
        assert "#FFEBEE" in style
        assert "#C62828" in style

        # O badge do cabeçalho não pode mostrar "ATIVA" quando a
        # organização está inativa - único ponto capturado pela regex,
        # já isolado do restante da página.
        assert text != "ATIVA"


class TestOrgDetailsRenderingIsReadOnly:
    def test_get_does_not_mutate_organization_or_create_audit_log(self, client, app, get_csrf_token):
        with app.app_context():
            org = _create_organization("Organizacao Issue 61 Somente Leitura", is_active=False)
            org_id = org.id

        # Login primeiro, baseline depois: o próprio login gera um
        # AuditLog (`user_login`) e um novo `User` (o admin) - isolar o
        # baseline após o login garante que a comparação abaixo mede
        # exclusivamente o efeito do GET em si, não do setup do teste.
        _create_admin_and_login(client, app, get_csrf_token)

        with app.app_context():
            audit_logs_before = AuditLog.query.count()
            organizations_before = Organization.query.count()
            members_before = OrganizationMember.query.count()
            users_before = User.query.count()

        response = client.get(f"/admin/organizations/{org_id}")
        assert response.status_code == 200

        with app.app_context():
            reloaded = Organization.query.get(org_id)
            assert reloaded.is_active is False

            assert AuditLog.query.count() == audit_logs_before
            assert Organization.query.count() == organizations_before
            assert OrganizationMember.query.count() == members_before
            assert User.query.count() == users_before
