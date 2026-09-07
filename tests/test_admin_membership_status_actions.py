"""Issue #60: o painel administrativo não indicava o status (`active`,
`suspended`, `removed`) dos vínculos de organização e mantinha "Papel"/
"Remover" disponíveis mesmo para vínculos não ativos; não havia rota
alguma para reverter um vínculo `removed` (`restore_removed_member`) ou
`suspended` (`reactivate_member`) pela interface.

Estes testes cobrem: exibição do status dos três estados na tabela
(sem filtrar nenhum), disponibilidade condicional de ações por status,
as duas novas rotas administrativas (restaurar/reativar) delegando
inteiramente para a camada de serviço já existente, rejeição de
`change_member_role` para vínculo não ativo, rejeição de
restauração/reativação em estado incompatível sem evento de sucesso,
preservação da mesma linha/id/histórico, exclusão do launcher para
vínculo `removed`, e autorização/CSRF nas novas rotas."""
from datetime import datetime

import pytest

from app.extensions import db
from app.models import AuditLog, Organization, OrganizationMember, Role, User
from app.models.identity import OrganizationMemberStatus
from app.services.organization_service import OrganizationError, OrganizationService

SYNTHETIC_PASSWORD = "senha-sintetica-issue-60-123"


def _create_user(email, *, name="Usuario Issue 60", is_internal_admin=False):
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


def _login(client, get_csrf_token, email):
    return client.post("/login", data={
        "email": email,
        "password": SYNTHETIC_PASSWORD,
        "csrf_token": get_csrf_token(client),
    })


def _create_organization(legal_name="Organizacao Issue 60"):
    org = Organization(legal_name=legal_name)
    db.session.add(org)
    db.session.commit()
    return org


def _create_admin_and_login(client, app, get_csrf_token, email="admin.issue60@example.com"):
    with app.app_context():
        _create_user(email, name="Admin Issue 60", is_internal_admin=True)
    _login(client, get_csrf_token, email)


def _ensure_roles(app):
    with app.app_context():
        owner_role = Role.query.filter_by(name="owner").first()
        if not owner_role:
            owner_role = Role(name="owner", description="Role owner")
            db.session.add(owner_role)
        member_role = Role.query.filter_by(name="member").first()
        if not member_role:
            member_role = Role(name="member", description="Role member")
            db.session.add(member_role)
        db.session.commit()


@pytest.fixture
def org_with_owner(app):
    """Organização com um owner ativo (evita disparar a proteção de
    'último owner ativo' ao suspender/remover o membro sob teste)."""
    _ensure_roles(app)
    with app.app_context():
        org = _create_organization()
        owner = _create_user("owner.fixo.issue60@example.com", name="Owner Fixo Issue 60")
        OrganizationService.add_member(org.id, owner.id, "owner")
        org_id = org.id
    return org_id


def _member_of(app, org_id, user_id):
    with app.app_context():
        return OrganizationMember.query.filter_by(organization_id=org_id, user_id=user_id).first()


# 1. Os três status aparecem no painel, sem filtragem -------------------------

class TestPanelDisplaysAllThreeStatuses:
    def test_active_suspended_removed_all_visible_with_status_badge(
        self, client, app, get_csrf_token, org_with_owner
    ):
        with app.app_context():
            active_user = _create_user("membro.ativo.issue60@example.com", name="Membro Ativo")
            suspended_user = _create_user("membro.suspenso.issue60@example.com", name="Membro Suspenso")
            removed_user = _create_user("membro.removido.issue60@example.com", name="Membro Removido")

            OrganizationService.add_member(org_with_owner, active_user.id, "member")
            OrganizationService.add_member(org_with_owner, suspended_user.id, "member")
            OrganizationService.add_member(org_with_owner, removed_user.id, "member")

            OrganizationService.suspend_member(org_with_owner, suspended_user.id)
            OrganizationService.remove_member(org_with_owner, removed_user.id)

        _create_admin_and_login(client, app, get_csrf_token)
        html = client.get(f"/admin/organizations/{org_with_owner}").data.decode("utf-8")

        # Nenhum vínculo foi filtrado da tabela.
        assert "membro.ativo.issue60@example.com" in html
        assert "membro.suspenso.issue60@example.com" in html
        assert "membro.removido.issue60@example.com" in html

        assert '<span class="member-status-badge member-status-active">ATIVO</span>' in html
        assert '<span class="member-status-badge member-status-suspended">SUSPENSO</span>' in html
        assert '<span class="member-status-badge member-status-removed">REMOVIDO</span>' in html


# 2-4. Ações disponíveis condicionadas ao status -------------------------------

class TestActionsAvailablePerStatus:
    def test_active_member_shows_role_select_and_remove_not_restore_or_reactivate(
        self, client, app, get_csrf_token, org_with_owner
    ):
        with app.app_context():
            user = _create_user("ativo.acoes.issue60@example.com")
            OrganizationService.add_member(org_with_owner, user.id, "member")
            user_id = user.id

        _create_admin_and_login(client, app, get_csrf_token)
        html = client.get(f"/admin/organizations/{org_with_owner}").data.decode("utf-8")

        assert f'/admin/organizations/{org_with_owner}/members/{user_id}/role' in html
        assert f'/admin/organizations/{org_with_owner}/members/{user_id}/remove' in html
        assert f'/admin/organizations/{org_with_owner}/members/{user_id}/restore' not in html
        assert f'/admin/organizations/{org_with_owner}/members/{user_id}/reactivate' not in html

    def test_removed_member_shows_restore_not_role_or_remove(
        self, client, app, get_csrf_token, org_with_owner
    ):
        with app.app_context():
            user = _create_user("removido.acoes.issue60@example.com")
            OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.remove_member(org_with_owner, user.id)
            user_id = user.id

        _create_admin_and_login(client, app, get_csrf_token)
        html = client.get(f"/admin/organizations/{org_with_owner}").data.decode("utf-8")

        assert f'/admin/organizations/{org_with_owner}/members/{user_id}/restore' in html
        assert f'/admin/organizations/{org_with_owner}/members/{user_id}/role' not in html
        assert f'/admin/organizations/{org_with_owner}/members/{user_id}/remove' not in html
        assert f'/admin/organizations/{org_with_owner}/members/{user_id}/reactivate' not in html

    def test_suspended_member_shows_reactivate_not_role_or_remove(
        self, client, app, get_csrf_token, org_with_owner
    ):
        with app.app_context():
            user = _create_user("suspenso.acoes.issue60@example.com")
            OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.suspend_member(org_with_owner, user.id)
            user_id = user.id

        _create_admin_and_login(client, app, get_csrf_token)
        html = client.get(f"/admin/organizations/{org_with_owner}").data.decode("utf-8")

        assert f'/admin/organizations/{org_with_owner}/members/{user_id}/reactivate' in html
        assert f'/admin/organizations/{org_with_owner}/members/{user_id}/role' not in html
        assert f'/admin/organizations/{org_with_owner}/members/{user_id}/remove' not in html
        assert f'/admin/organizations/{org_with_owner}/members/{user_id}/restore' not in html


# 5-9. Rotas de restauração e reativação ---------------------------------------

class TestRestoreRoute:
    def test_restore_via_http_sets_removed_to_active(self, client, app, get_csrf_token, org_with_owner):
        with app.app_context():
            user = _create_user("restaurar.http.issue60@example.com")
            OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.remove_member(org_with_owner, user.id)
            user_id = user.id

        _create_admin_and_login(client, app, get_csrf_token)
        response = client.post(
            f"/admin/organizations/{org_with_owner}/members/{user_id}/restore",
            data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_with_owner}")},
        )
        assert response.status_code == 302

        member = _member_of(app, org_with_owner, user_id)
        assert member.status == "active"

    def test_restore_preserves_same_row_id_and_created_at(self, client, app, get_csrf_token, org_with_owner):
        with app.app_context():
            user = _create_user("restaurar.preserva.issue60@example.com")
            member = OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.remove_member(org_with_owner, user.id)
            original_id = member.id
            original_created_at = member.created_at
            user_id = user.id

        _create_admin_and_login(client, app, get_csrf_token)
        client.post(
            f"/admin/organizations/{org_with_owner}/members/{user_id}/restore",
            data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_with_owner}")},
        )

        with app.app_context():
            assert OrganizationMember.query.filter_by(organization_id=org_with_owner, user_id=user_id).count() == 1
            reloaded = OrganizationMember.query.get(original_id)
            assert reloaded is not None
            assert reloaded.id == original_id
            assert reloaded.created_at == original_created_at
            assert reloaded.status == "active"

    def test_restore_creates_exactly_one_restored_audit_log(self, client, app, get_csrf_token, org_with_owner):
        with app.app_context():
            user = _create_user("restaurar.auditoria.issue60@example.com")
            OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.remove_member(org_with_owner, user.id)
            user_id = user.id
            before = AuditLog.query.filter_by(action="organization.member.restored").count()

        _create_admin_and_login(client, app, get_csrf_token)
        client.post(
            f"/admin/organizations/{org_with_owner}/members/{user_id}/restore",
            data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_with_owner}")},
        )

        with app.app_context():
            assert AuditLog.query.filter_by(action="organization.member.restored").count() == before + 1


class TestReactivateRoute:
    def test_reactivate_via_http_sets_suspended_to_active(self, client, app, get_csrf_token, org_with_owner):
        with app.app_context():
            user = _create_user("reativar.http.issue60@example.com")
            OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.suspend_member(org_with_owner, user.id)
            user_id = user.id

        _create_admin_and_login(client, app, get_csrf_token)
        response = client.post(
            f"/admin/organizations/{org_with_owner}/members/{user_id}/reactivate",
            data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_with_owner}")},
        )
        assert response.status_code == 302

        member = _member_of(app, org_with_owner, user_id)
        assert member.status == "active"

    def test_reactivate_preserves_same_row_id_and_created_at(self, client, app, get_csrf_token, org_with_owner):
        with app.app_context():
            user = _create_user("reativar.preserva.issue60@example.com")
            member = OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.suspend_member(org_with_owner, user.id)
            original_id = member.id
            original_created_at = member.created_at
            user_id = user.id

        _create_admin_and_login(client, app, get_csrf_token)
        client.post(
            f"/admin/organizations/{org_with_owner}/members/{user_id}/reactivate",
            data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_with_owner}")},
        )

        with app.app_context():
            assert OrganizationMember.query.filter_by(organization_id=org_with_owner, user_id=user_id).count() == 1
            reloaded = OrganizationMember.query.get(original_id)
            assert reloaded is not None
            assert reloaded.id == original_id
            assert reloaded.created_at == original_created_at
            assert reloaded.status == "active"

    def test_reactivate_creates_exactly_one_reactivated_audit_log(self, client, app, get_csrf_token, org_with_owner):
        with app.app_context():
            user = _create_user("reativar.auditoria.issue60@example.com")
            OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.suspend_member(org_with_owner, user.id)
            user_id = user.id
            before = AuditLog.query.filter_by(action="organization.member.reactivated").count()

        _create_admin_and_login(client, app, get_csrf_token)
        client.post(
            f"/admin/organizations/{org_with_owner}/members/{user_id}/reactivate",
            data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_with_owner}")},
        )

        with app.app_context():
            assert AuditLog.query.filter_by(action="organization.member.reactivated").count() == before + 1


# 10. Repetição/estado incompatível é rejeitado sem evento de sucesso ---------

class TestIncompatibleStateRejectedWithoutSuccessEvent:
    def test_restore_rejected_when_member_already_active(self, app, org_with_owner):
        with app.app_context():
            user = _create_user("restaurar.ja.ativo.issue60@example.com")
            OrganizationService.add_member(org_with_owner, user.id, "member")
            before = AuditLog.query.filter_by(action="organization.member.restored").count()

            with pytest.raises(OrganizationError):
                OrganizationService.restore_removed_member(org_with_owner, user.id)

            member = OrganizationMember.query.filter_by(organization_id=org_with_owner, user_id=user.id).first()
            assert member.status == "active"
            assert AuditLog.query.filter_by(action="organization.member.restored").count() == before

    def test_reactivate_rejected_when_member_already_active(self, app, org_with_owner):
        with app.app_context():
            user = _create_user("reativar.ja.ativo.issue60@example.com")
            OrganizationService.add_member(org_with_owner, user.id, "member")
            before = AuditLog.query.filter_by(action="organization.member.reactivated").count()

            with pytest.raises(OrganizationError):
                OrganizationService.reactivate_member(org_with_owner, user.id)

            member = OrganizationMember.query.filter_by(organization_id=org_with_owner, user_id=user.id).first()
            assert member.status == "active"
            assert AuditLog.query.filter_by(action="organization.member.reactivated").count() == before

    def test_reactivate_rejected_when_member_removed(self, app, org_with_owner):
        with app.app_context():
            user = _create_user("reativar.removido.issue60@example.com")
            OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.remove_member(org_with_owner, user.id)
            before = AuditLog.query.filter_by(action="organization.member.reactivated").count()

            with pytest.raises(OrganizationError):
                OrganizationService.reactivate_member(org_with_owner, user.id)

            member = OrganizationMember.query.filter_by(organization_id=org_with_owner, user_id=user.id).first()
            assert member.status == "removed"
            assert AuditLog.query.filter_by(action="organization.member.reactivated").count() == before

    def test_restore_rejected_when_member_suspended(self, app, org_with_owner):
        with app.app_context():
            user = _create_user("restaurar.suspenso.issue60@example.com")
            OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.suspend_member(org_with_owner, user.id)
            before = AuditLog.query.filter_by(action="organization.member.restored").count()

            with pytest.raises(OrganizationError):
                OrganizationService.restore_removed_member(org_with_owner, user.id)

            member = OrganizationMember.query.filter_by(organization_id=org_with_owner, user_id=user.id).first()
            assert member.status == "suspended"
            assert AuditLog.query.filter_by(action="organization.member.restored").count() == before


# 11. change_member_role rejeitado para vínculo não ativo ---------------------

class TestChangeRoleRejectedForNonActiveMembership:
    def test_change_role_rejected_for_removed_member_without_mutation(self, app, org_with_owner):
        with app.app_context():
            user = _create_user("papel.removido.issue60@example.com")
            member = OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.remove_member(org_with_owner, user.id)
            original_role_id = member.role_id
            before = AuditLog.query.filter_by(action="organization.member.role_changed").count()

            with pytest.raises(OrganizationError, match="vínculos ativos"):
                OrganizationService.change_member_role(org_with_owner, user.id, "owner")

            reloaded = OrganizationMember.query.filter_by(organization_id=org_with_owner, user_id=user.id).first()
            assert reloaded.role_id == original_role_id
            assert reloaded.status == "removed"
            assert AuditLog.query.filter_by(action="organization.member.role_changed").count() == before

    def test_change_role_rejected_for_suspended_member_without_mutation(self, app, org_with_owner):
        with app.app_context():
            user = _create_user("papel.suspenso.issue60@example.com")
            member = OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.suspend_member(org_with_owner, user.id)
            original_role_id = member.role_id
            before = AuditLog.query.filter_by(action="organization.member.role_changed").count()

            with pytest.raises(OrganizationError, match="vínculos ativos"):
                OrganizationService.change_member_role(org_with_owner, user.id, "owner")

            reloaded = OrganizationMember.query.filter_by(organization_id=org_with_owner, user_id=user.id).first()
            assert reloaded.role_id == original_role_id
            assert reloaded.status == "suspended"
            assert AuditLog.query.filter_by(action="organization.member.role_changed").count() == before

    def test_change_role_route_rejected_for_removed_member_without_mutation(
        self, client, app, get_csrf_token, org_with_owner
    ):
        with app.app_context():
            user = _create_user("papel.rota.removido.issue60@example.com")
            member = OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.remove_member(org_with_owner, user.id)
            original_role_id = member.role_id
            user_id = user.id

        _create_admin_and_login(client, app, get_csrf_token)
        response = client.post(
            f"/admin/organizations/{org_with_owner}/members/{user_id}/role",
            data={
                "role": "owner",
                "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_with_owner}"),
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "vínculos ativos" in response.data.decode("utf-8")

        with app.app_context():
            reloaded = OrganizationMember.query.filter_by(organization_id=org_with_owner, user_id=user_id).first()
            assert reloaded.role_id == original_role_id
            assert reloaded.status == "removed"


# 12. vínculo removed não aparece como organização ativa no launcher ----------

class TestRemovedMembershipExcludedFromLauncher:
    def test_removed_membership_excluded_from_get_user_organizations(self, app, org_with_owner):
        with app.app_context():
            user = _create_user("launcher.removido.issue60@example.com")
            OrganizationService.add_member(org_with_owner, user.id, "member")
            assert any(
                org.id == org_with_owner
                for org in OrganizationService.get_user_organizations(user.id)
            )

            OrganizationService.remove_member(org_with_owner, user.id)

            orgs_after = OrganizationService.get_user_organizations(user.id)
            assert all(org.id != org_with_owner for org in orgs_after)


# 13. Autorização nas novas rotas ----------------------------------------------

class TestAuthorizationOnNewRoutes:
    def test_restore_route_requires_authentication(self, client, app, get_csrf_token, org_with_owner):
        with app.app_context():
            user = _create_user("restaurar.sem.login.issue60@example.com")
            OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.remove_member(org_with_owner, user.id)
            user_id = user.id

        # Token CSRF válido obtido sem autenticação (página pública) - prova
        # que é login_required, não CSRF, quem barra o acesso aqui (mesmo
        # padrão de test_valid_token_without_login_does_not_authorize_admin_route
        # em tests/test_csrf_protection.py).
        token = get_csrf_token(client, "/login")
        response = client.post(
            f"/admin/organizations/{org_with_owner}/members/{user_id}/restore",
            data={"csrf_token": token},
        )
        assert response.status_code == 302
        assert "/login" in response.location

        member = _member_of(app, org_with_owner, user_id)
        assert member.status == "removed"

    def test_reactivate_route_requires_authentication(self, client, app, get_csrf_token, org_with_owner):
        with app.app_context():
            user = _create_user("reativar.sem.login.issue60@example.com")
            OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.suspend_member(org_with_owner, user.id)
            user_id = user.id

        token = get_csrf_token(client, "/login")
        response = client.post(
            f"/admin/organizations/{org_with_owner}/members/{user_id}/reactivate",
            data={"csrf_token": token},
        )
        assert response.status_code == 302
        assert "/login" in response.location

        member = _member_of(app, org_with_owner, user_id)
        assert member.status == "suspended"

    def test_restore_route_rejects_non_internal_admin(self, client, app, get_csrf_token, org_with_owner):
        with app.app_context():
            regular_email = "regular.restaurar.issue60@example.com"
            _create_user(regular_email, name="Usuario Comum Issue 60", is_internal_admin=False)
            target = _create_user("alvo.restaurar.naoadmin.issue60@example.com")
            OrganizationService.add_member(org_with_owner, target.id, "member")
            OrganizationService.remove_member(org_with_owner, target.id)
            target_id = target.id
            before_audit = AuditLog.query.filter_by(action="organization.member.restored").count()

        _login(client, get_csrf_token, regular_email)
        response = client.post(
            f"/admin/organizations/{org_with_owner}/members/{target_id}/restore",
            data={"csrf_token": get_csrf_token(client, "/login")},
        )
        assert response.status_code == 302
        # Destino exato de internal_admin_required (redirect(url_for(
        # 'dashboard.index'))) - confirmado empiricamente como '/' nesta
        # aplicação. Validação positiva, não apenas "não é /login": prova
        # que é a checagem de administrador interno rejeitando, e não
        # qualquer outro redirecionamento coincidente com status 302.
        assert response.location == "/"

        member = _member_of(app, org_with_owner, target_id)
        assert member.status == "removed"
        with app.app_context():
            assert AuditLog.query.filter_by(action="organization.member.restored").count() == before_audit

    def test_reactivate_route_rejects_non_internal_admin(self, client, app, get_csrf_token, org_with_owner):
        with app.app_context():
            regular_email = "regular.reativar.issue60@example.com"
            _create_user(regular_email, name="Usuario Comum Issue 60", is_internal_admin=False)
            target = _create_user("alvo.reativar.naoadmin.issue60@example.com")
            OrganizationService.add_member(org_with_owner, target.id, "member")
            OrganizationService.suspend_member(org_with_owner, target.id)
            target_id = target.id
            before_audit = AuditLog.query.filter_by(action="organization.member.reactivated").count()

        _login(client, get_csrf_token, regular_email)
        response = client.post(
            f"/admin/organizations/{org_with_owner}/members/{target_id}/reactivate",
            data={"csrf_token": get_csrf_token(client, "/login")},
        )
        assert response.status_code == 302
        assert response.location == "/"

        member = _member_of(app, org_with_owner, target_id)
        assert member.status == "suspended"
        with app.app_context():
            assert AuditLog.query.filter_by(action="organization.member.reactivated").count() == before_audit


# 14. CSRF nas novas rotas ------------------------------------------------------

class TestCsrfOnNewRoutes:
    def test_restore_without_csrf_token_returns_400(self, client, app, get_csrf_token, org_with_owner):
        with app.app_context():
            user = _create_user("restaurar.sem.csrf.issue60@example.com")
            OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.remove_member(org_with_owner, user.id)
            user_id = user.id

        _create_admin_and_login(client, app, get_csrf_token)
        response = client.post(f"/admin/organizations/{org_with_owner}/members/{user_id}/restore", data={})
        assert response.status_code == 400

        member = _member_of(app, org_with_owner, user_id)
        assert member.status == "removed"

    def test_reactivate_without_csrf_token_returns_400(self, client, app, get_csrf_token, org_with_owner):
        with app.app_context():
            user = _create_user("reativar.sem.csrf.issue60@example.com")
            OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.suspend_member(org_with_owner, user.id)
            user_id = user.id

        _create_admin_and_login(client, app, get_csrf_token)
        response = client.post(f"/admin/organizations/{org_with_owner}/members/{user_id}/reactivate", data={})
        assert response.status_code == 400

        member = _member_of(app, org_with_owner, user_id)
        assert member.status == "suspended"


# GET não pode executar mutação -------------------------------------------------

class TestGetIsNotAllowedForMutations:
    def test_restore_route_rejects_get(self, client, app, get_csrf_token, org_with_owner):
        with app.app_context():
            user = _create_user("restaurar.get.issue60@example.com")
            OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.remove_member(org_with_owner, user.id)
            user_id = user.id

        _create_admin_and_login(client, app, get_csrf_token)
        response = client.get(f"/admin/organizations/{org_with_owner}/members/{user_id}/restore")
        assert response.status_code == 405

        member = _member_of(app, org_with_owner, user_id)
        assert member.status == "removed"

    def test_reactivate_route_rejects_get(self, client, app, get_csrf_token, org_with_owner):
        with app.app_context():
            user = _create_user("reativar.get.issue60@example.com")
            OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.suspend_member(org_with_owner, user.id)
            user_id = user.id

        _create_admin_and_login(client, app, get_csrf_token)
        response = client.get(f"/admin/organizations/{org_with_owner}/members/{user_id}/reactivate")
        assert response.status_code == 405

        member = _member_of(app, org_with_owner, user_id)
        assert member.status == "suspended"


# 15. Nenhuma linha física é apagada --------------------------------------------

class TestNoPhysicalRowDeletion:
    def test_restore_does_not_delete_or_recreate_row(self, client, app, get_csrf_token, org_with_owner):
        with app.app_context():
            user = _create_user("nao.apaga.restaurar.issue60@example.com")
            member = OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.remove_member(org_with_owner, user.id)
            before_total = OrganizationMember.query.count()
            original_id = member.id
            user_id = user.id

        _create_admin_and_login(client, app, get_csrf_token)
        client.post(
            f"/admin/organizations/{org_with_owner}/members/{user_id}/restore",
            data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_with_owner}")},
        )

        with app.app_context():
            assert OrganizationMember.query.count() == before_total
            assert OrganizationMember.query.get(original_id) is not None

    def test_reactivate_does_not_delete_or_recreate_row(self, client, app, get_csrf_token, org_with_owner):
        with app.app_context():
            user = _create_user("nao.apaga.reativar.issue60@example.com")
            member = OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.suspend_member(org_with_owner, user.id)
            before_total = OrganizationMember.query.count()
            original_id = member.id
            user_id = user.id

        _create_admin_and_login(client, app, get_csrf_token)
        client.post(
            f"/admin/organizations/{org_with_owner}/members/{user_id}/reactivate",
            data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_with_owner}")},
        )

        with app.app_context():
            assert OrganizationMember.query.count() == before_total
            assert OrganizationMember.query.get(original_id) is not None


# A1 (revisão técnica): cobertura HTTP de estado incompatível por repetição --
# Complementa (não substitui) TestIncompatibleStateRejectedWithoutSuccessEvent
# acima, que continua cobrindo a regra diretamente na camada de serviço.
# Aqui, a mesma rejeição é comprovada atravessando efetivamente o blueprint
# (autenticação de administrador interno + CSRF real via client.post), com
# uma segunda requisição sobre um vínculo já restaurado/reativado pela
# primeira.

class TestRepeatedHttpRequestRejectedWithoutSuccessEvent:
    def test_second_restore_via_http_is_rejected_without_additional_mutation_or_event(
        self, client, app, get_csrf_token, org_with_owner
    ):
        with app.app_context():
            user = _create_user("restaurar.repetido.http.issue60@example.com")
            member = OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.remove_member(org_with_owner, user.id)
            original_id = member.id
            user_id = user.id

        _create_admin_and_login(client, app, get_csrf_token)
        restore_url = f"/admin/organizations/{org_with_owner}/members/{user_id}/restore"

        first_response = client.post(
            restore_url,
            data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_with_owner}")},
        )
        assert first_response.status_code == 302

        with app.app_context():
            audit_after_first = AuditLog.query.filter_by(action="organization.member.restored").count()
            assert OrganizationMember.query.get(original_id).status == "active"

        second_response = client.post(
            restore_url,
            data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_with_owner}")},
            follow_redirects=True,
        )

        # A rota sempre responde com redirect (mesmo em rejeição, o erro de
        # domínio é convertido em flash + redirect, nunca em status de
        # erro) - `history` prova que o 302 esperado de fato ocorreu antes
        # da página final ser servida.
        assert len(second_response.history) == 1
        assert second_response.history[0].status_code == 302
        assert second_response.status_code == 200
        assert (
            "Esta operação só é permitida para vínculos removidos."
            in second_response.data.decode("utf-8")
        )

        with app.app_context():
            assert (
                AuditLog.query.filter_by(action="organization.member.restored").count()
                == audit_after_first
            )
            assert OrganizationMember.query.filter_by(organization_id=org_with_owner, user_id=user_id).count() == 1
            reloaded = OrganizationMember.query.get(original_id)
            assert reloaded is not None
            assert reloaded.id == original_id
            assert reloaded.status == "active"

    def test_second_reactivate_via_http_is_rejected_without_additional_mutation_or_event(
        self, client, app, get_csrf_token, org_with_owner
    ):
        with app.app_context():
            user = _create_user("reativar.repetido.http.issue60@example.com")
            member = OrganizationService.add_member(org_with_owner, user.id, "member")
            OrganizationService.suspend_member(org_with_owner, user.id)
            original_id = member.id
            user_id = user.id

        _create_admin_and_login(client, app, get_csrf_token)
        reactivate_url = f"/admin/organizations/{org_with_owner}/members/{user_id}/reactivate"

        first_response = client.post(
            reactivate_url,
            data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_with_owner}")},
        )
        assert first_response.status_code == 302

        with app.app_context():
            audit_after_first = AuditLog.query.filter_by(action="organization.member.reactivated").count()
            assert OrganizationMember.query.get(original_id).status == "active"

        second_response = client.post(
            reactivate_url,
            data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_with_owner}")},
            follow_redirects=True,
        )

        assert len(second_response.history) == 1
        assert second_response.history[0].status_code == 302
        assert second_response.status_code == 200
        assert "O vínculo já está neste status." in second_response.data.decode("utf-8")

        with app.app_context():
            assert (
                AuditLog.query.filter_by(action="organization.member.reactivated").count()
                == audit_after_first
            )
            assert OrganizationMember.query.filter_by(organization_id=org_with_owner, user_id=user_id).count() == 1
            reloaded = OrganizationMember.query.get(original_id)
            assert reloaded is not None
            assert reloaded.id == original_id
            assert reloaded.status == "active"
