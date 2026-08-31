from datetime import datetime
from unittest.mock import MagicMock

import pytest

from app.extensions import db
from app.models import AuditLog, Organization, OrganizationMember, OrganizationProduct, Product, Role, User
from app.models.identity import OrganizationMemberStatus
from app.services.organization_service import OrganizationService
from app.services.access_service import AccessService

SYNTHETIC_PASSWORD = "senha-sintetica-politica-123"


@pytest.fixture
def policy_setup(app):
    with app.app_context():
        owner_role = Role(name="owner", description="Role owner")
        member_role = Role(name="member", description="Role member")
        db.session.add_all([owner_role, member_role])
        db.session.flush()

        org = Organization(legal_name="Organizacao Teste Politica Admin Interno")
        db.session.add(org)
        db.session.flush()

        regular_user = User(name="Usuario Comum Politica", email="usuario.comum.politica@example.com")
        regular_user.set_password(SYNTHETIC_PASSWORD)
        db.session.add(regular_user)

        internal_admin = User(
            name="Admin Interno Politica",
            email="admin.interno.politica@example.com",
            is_internal_admin=True,
            email_verified_at=datetime.utcnow(),
        )
        internal_admin.set_password(SYNTHETIC_PASSWORD)
        db.session.add(internal_admin)
        db.session.flush()

        db.session.commit()

        yield {
            "org": org,
            "owner_role": owner_role,
            "member_role": member_role,
            "regular_user": regular_user,
            "internal_admin": internal_admin,
        }


def _create_legacy_membership(org_id, user_id, role_id, status):
    """Simula um vínculo legado (criado antes desta política existir),
    contornando deliberadamente `OrganizationService.add_member` - que já
    rejeita a criação de um novo vínculo para administrador interno -
    para poder testar o comportamento sobre um registro pré-existente."""
    member = OrganizationMember(
        user_id=user_id,
        organization_id=org_id,
        role_id=role_id,
        status=status,
    )
    db.session.add(member)
    db.session.commit()
    return member


class TestAddMemberRejectsInternalAdmin:
    def test_rejects_internal_admin_as_owner(self, policy_setup):
        with pytest.raises(ValueError, match="Administradores internos"):
            OrganizationService.add_member(
                policy_setup["org"].id, policy_setup["internal_admin"].id, "owner"
            )

    def test_rejects_internal_admin_as_member(self, policy_setup):
        with pytest.raises(ValueError, match="Administradores internos"):
            OrganizationService.add_member(
                policy_setup["org"].id, policy_setup["internal_admin"].id, "member"
            )

    def test_regular_user_can_still_be_added(self, policy_setup):
        member = OrganizationService.add_member(
            policy_setup["org"].id, policy_setup["regular_user"].id, "member"
        )
        assert member is not None
        assert member.status == OrganizationMemberStatus.ACTIVE.value

    def test_rejection_creates_no_missing_role(self, policy_setup):
        with pytest.raises(ValueError):
            OrganizationService.add_member(
                policy_setup["org"].id, policy_setup["internal_admin"].id, "brand-new-role-name"
            )

        assert Role.query.filter_by(name="brand-new-role-name").first() is None

    def test_rejection_creates_no_organization_member(self, policy_setup):
        with pytest.raises(ValueError):
            OrganizationService.add_member(
                policy_setup["org"].id, policy_setup["internal_admin"].id, "owner"
            )

        assert OrganizationMember.query.filter_by(
            user_id=policy_setup["internal_admin"].id
        ).count() == 0

    def test_rejection_creates_no_audit_log(self, policy_setup):
        before = AuditLog.query.count()

        with pytest.raises(ValueError):
            OrganizationService.add_member(
                policy_setup["org"].id, policy_setup["internal_admin"].id, "owner"
            )

        assert AuditLog.query.count() == before

    def test_rejection_executes_zero_commits(self, policy_setup, monkeypatch):
        commit_calls = []
        original_commit = db.session.commit

        def _counting_commit():
            commit_calls.append(1)
            return original_commit()

        monkeypatch.setattr(db.session, "commit", _counting_commit)

        with pytest.raises(ValueError):
            OrganizationService.add_member(
                policy_setup["org"].id, policy_setup["internal_admin"].id, "owner"
            )

        assert len(commit_calls) == 0


class TestForgedPostIsRejectedEvenIgnoringTemplate:
    """A rejeição vive no serviço (OrganizationService.add_member), não no
    template - por isso um POST forjado diretamente para a rota, com o
    user_id de um administrador interno que nunca apareceria no seletor
    (excluído apenas visualmente em org_details.html), também é
    rejeitado."""

    def _login(self, client, email):
        return client.post("/login", data={"email": email, "password": SYNTHETIC_PASSWORD})

    def test_forged_add_member_post_is_rejected(self, client, app):
        with app.app_context():
            owner_role = Role(name="owner", description="Role owner")
            db.session.add(owner_role)
            db.session.flush()

            org = Organization(legal_name="Organizacao Forjada")
            db.session.add(org)
            db.session.flush()

            operator = User(
                name="Operador Interno",
                email="operador.interno.forjado@example.com",
                is_internal_admin=True,
                email_verified_at=datetime.utcnow(),
            )
            operator.set_password(SYNTHETIC_PASSWORD)

            target_admin = User(
                name="Admin Alvo Forjado",
                email="admin.alvo.forjado@example.com",
                is_internal_admin=True,
                email_verified_at=datetime.utcnow(),
            )
            target_admin.set_password(SYNTHETIC_PASSWORD)

            db.session.add_all([operator, target_admin])
            db.session.commit()

            org_id = org.id
            target_admin_id = target_admin.id

        self._login(client, "operador.interno.forjado@example.com")

        # POST direto para a rota, com o user_id de um administrador
        # interno - exatamente o que o template nunca ofereceria como
        # opção, simulando um POST forjado que ignore a UI por completo.
        response = client.post(
            f"/admin/organizations/{org_id}/members",
            data={"user_id": str(target_admin_id), "role": "owner"},
        )

        assert response.status_code in (302, 303)
        with app.app_context():
            assert OrganizationMember.query.filter_by(user_id=target_admin_id).count() == 0

    def test_forged_link_user_to_org_post_is_rejected(self, client, app):
        with app.app_context():
            owner_role = Role(name="owner", description="Role owner")
            db.session.add(owner_role)
            db.session.flush()

            org = Organization(legal_name="Organizacao Forjada Link")
            db.session.add(org)
            db.session.flush()

            operator = User(
                name="Operador Interno Link",
                email="operador.interno.link@example.com",
                is_internal_admin=True,
                email_verified_at=datetime.utcnow(),
            )
            operator.set_password(SYNTHETIC_PASSWORD)

            target_admin = User(
                name="Admin Alvo Link",
                email="admin.alvo.link@example.com",
                is_internal_admin=True,
                email_verified_at=datetime.utcnow(),
            )
            target_admin.set_password(SYNTHETIC_PASSWORD)

            db.session.add_all([operator, target_admin])
            db.session.commit()

            target_admin_id = target_admin.id
            org_id = org.id

        self._login(client, "operador.interno.link@example.com")

        response = client.post(
            f"/admin/users/{target_admin_id}/organization",
            data={"organization_id": str(org_id), "role": "owner"},
        )

        assert response.status_code in (302, 303)
        with app.app_context():
            assert OrganizationMember.query.filter_by(user_id=target_admin_id).count() == 0


class TestTemplateStillOmitsInternalAdminsVisually:
    def test_internal_admin_does_not_appear_in_org_details_page(self, client, app):
        with app.app_context():
            org = Organization(legal_name="Organizacao Template Teste")
            db.session.add(org)
            db.session.flush()

            operator = User(
                name="Operador Template",
                email="operador.template@example.com",
                is_internal_admin=True,
                email_verified_at=datetime.utcnow(),
            )
            operator.set_password(SYNTHETIC_PASSWORD)

            other_admin = User(
                name="Outro Admin Interno Nao Deve Aparecer",
                email="outro.admin.nao.aparece@example.com",
                is_internal_admin=True,
                email_verified_at=datetime.utcnow(),
            )
            other_admin.set_password(SYNTHETIC_PASSWORD)

            db.session.add_all([operator, other_admin])
            db.session.commit()
            org_id = org.id

        client.post("/login", data={"email": "operador.template@example.com", "password": SYNTHETIC_PASSWORD})

        response = client.get(f"/admin/organizations/{org_id}")

        assert response.status_code == 200
        assert b"outro.admin.nao.aparece@example.com" not in response.data


class TestReactivationAndRestorationRejectInternalAdminLegacyMembership:
    def test_reactivate_member_rejects_legacy_suspended_internal_admin(self, policy_setup):
        org_id = policy_setup["org"].id
        admin_id = policy_setup["internal_admin"].id
        legacy = _create_legacy_membership(
            org_id, admin_id, policy_setup["member_role"].id, OrganizationMemberStatus.SUSPENDED.value
        )
        legacy_id = legacy.id

        with pytest.raises(ValueError, match="Administradores internos"):
            OrganizationService.reactivate_member(org_id, admin_id)

        db.session.remove()
        reloaded = OrganizationMember.query.filter_by(id=legacy_id).first()
        assert reloaded.status == OrganizationMemberStatus.SUSPENDED.value

    def test_restore_removed_member_rejects_legacy_removed_internal_admin(self, policy_setup):
        org_id = policy_setup["org"].id
        admin_id = policy_setup["internal_admin"].id
        legacy = _create_legacy_membership(
            org_id, admin_id, policy_setup["member_role"].id, OrganizationMemberStatus.REMOVED.value
        )
        legacy_id = legacy.id

        with pytest.raises(ValueError, match="Administradores internos"):
            OrganizationService.restore_removed_member(org_id, admin_id)

        db.session.remove()
        reloaded = OrganizationMember.query.filter_by(id=legacy_id).first()
        assert reloaded.status == OrganizationMemberStatus.REMOVED.value

    def test_reactivation_rejection_creates_no_audit_log(self, policy_setup):
        _create_legacy_membership(
            policy_setup["org"].id,
            policy_setup["internal_admin"].id,
            policy_setup["member_role"].id,
            OrganizationMemberStatus.SUSPENDED.value,
        )
        before = AuditLog.query.count()

        with pytest.raises(ValueError):
            OrganizationService.reactivate_member(policy_setup["org"].id, policy_setup["internal_admin"].id)

        assert AuditLog.query.count() == before

    def test_reactivation_rejection_executes_zero_commits(self, policy_setup, monkeypatch):
        _create_legacy_membership(
            policy_setup["org"].id,
            policy_setup["internal_admin"].id,
            policy_setup["member_role"].id,
            OrganizationMemberStatus.SUSPENDED.value,
        )

        commit_calls = []
        original_commit = db.session.commit

        def _counting_commit():
            commit_calls.append(1)
            return original_commit()

        monkeypatch.setattr(db.session, "commit", _counting_commit)

        with pytest.raises(ValueError):
            OrganizationService.reactivate_member(policy_setup["org"].id, policy_setup["internal_admin"].id)

        assert len(commit_calls) == 0


class TestChangeMemberRoleRejectsInternalAdmin:
    """Um vínculo (mesmo legado) de administrador interno nunca pode ter o
    papel promovido/rebaixado/reorganizado - isso o trataria como um
    vínculo legítimo."""

    def test_rejects_role_change_on_legacy_active_internal_admin(self, policy_setup):
        org_id = policy_setup["org"].id
        admin_id = policy_setup["internal_admin"].id
        member_role_id = policy_setup["member_role"].id
        legacy = _create_legacy_membership(org_id, admin_id, member_role_id, OrganizationMemberStatus.ACTIVE.value)
        legacy_id = legacy.id

        with pytest.raises(ValueError, match="Administradores internos"):
            OrganizationService.change_member_role(org_id, admin_id, "owner")

        db.session.remove()
        reloaded = OrganizationMember.query.filter_by(id=legacy_id).first()
        assert reloaded.role_id == member_role_id

    def test_rejection_creates_no_audit_log(self, policy_setup):
        org_id = policy_setup["org"].id
        admin_id = policy_setup["internal_admin"].id
        _create_legacy_membership(
            org_id, admin_id, policy_setup["member_role"].id, OrganizationMemberStatus.ACTIVE.value
        )
        before = AuditLog.query.count()

        with pytest.raises(ValueError):
            OrganizationService.change_member_role(org_id, admin_id, "owner")

        assert AuditLog.query.count() == before

    def test_rejection_executes_zero_commits(self, policy_setup, monkeypatch):
        org_id = policy_setup["org"].id
        admin_id = policy_setup["internal_admin"].id
        _create_legacy_membership(
            org_id, admin_id, policy_setup["member_role"].id, OrganizationMemberStatus.ACTIVE.value
        )

        commit_calls = []
        original_commit = db.session.commit

        def _counting_commit():
            commit_calls.append(1)
            return original_commit()

        monkeypatch.setattr(db.session, "commit", _counting_commit)

        with pytest.raises(ValueError):
            OrganizationService.change_member_role(org_id, admin_id, "owner")

        assert len(commit_calls) == 0

    def test_regular_user_role_change_still_works(self, policy_setup):
        org_id = policy_setup["org"].id
        user_id = policy_setup["regular_user"].id
        OrganizationService.add_member(org_id, user_id, "member")

        member = OrganizationService.change_member_role(org_id, user_id, "owner")

        assert member.role_id == policy_setup["owner_role"].id


class TestSanitizationOfLegacyMembershipRemainsPossible:
    """Suspender/remover um vínculo legado inválido de administrador
    interno precisa continuar funcionando - é o único jeito de neutralizar
    o vínculo sem apagar/alterar a linha silenciosamente."""

    def test_suspend_member_still_works_on_legacy_active_internal_admin(self, policy_setup):
        org_id = policy_setup["org"].id
        admin_id = policy_setup["internal_admin"].id
        legacy = _create_legacy_membership(
            org_id, admin_id, policy_setup["member_role"].id, OrganizationMemberStatus.ACTIVE.value
        )
        legacy_id = legacy.id

        OrganizationService.suspend_member(org_id, admin_id)

        db.session.remove()
        reloaded = OrganizationMember.query.filter_by(id=legacy_id).first()
        assert reloaded.status == OrganizationMemberStatus.SUSPENDED.value

    def test_remove_member_still_works_on_legacy_active_internal_admin(self, policy_setup):
        org_id = policy_setup["org"].id
        admin_id = policy_setup["internal_admin"].id
        legacy = _create_legacy_membership(
            org_id, admin_id, policy_setup["member_role"].id, OrganizationMemberStatus.ACTIVE.value
        )
        legacy_id = legacy.id

        OrganizationService.remove_member(org_id, admin_id)

        db.session.remove()
        reloaded = OrganizationMember.query.filter_by(id=legacy_id).first()
        assert reloaded.status == OrganizationMemberStatus.REMOVED.value


class TestAccessServiceDeniesLegacyInternalAdminMembership:
    def test_get_active_membership_returns_none_for_legacy_active_internal_admin(self, policy_setup):
        _create_legacy_membership(
            policy_setup["org"].id,
            policy_setup["internal_admin"].id,
            policy_setup["owner_role"].id,
            OrganizationMemberStatus.ACTIVE.value,
        )

        result = OrganizationService.get_active_membership(
            policy_setup["internal_admin"].id, policy_setup["org"].id
        )
        assert result is None

    def test_access_service_denies_product_access_for_legacy_active_internal_admin(self, policy_setup):
        _create_legacy_membership(
            policy_setup["org"].id,
            policy_setup["internal_admin"].id,
            policy_setup["owner_role"].id,
            OrganizationMemberStatus.ACTIVE.value,
        )
        product = Product(code="policy-test-product", name="Produto Teste Politica", url="https://policy-test.local")
        db.session.add(product)
        db.session.flush()
        org_product = OrganizationProduct(
            organization_id=policy_setup["org"].id, product_id=product.id, status="active"
        )
        db.session.add(org_product)
        db.session.commit()

        with pytest.raises(ValueError) as exc_info:
            AccessService.get_organization_products(policy_setup["internal_admin"].id, policy_setup["org"].id)
        # Confirma o tipo exato de erro esperado pelo contrato (ValueError,
        # o mesmo já usado para "sem vínculo ativo") - nunca uma exceção
        # diferente que pudesse escapar sem tratamento em uma rota.
        assert isinstance(exc_info.value, ValueError)

    def test_access_service_does_not_query_products_after_rejection(self, policy_setup, monkeypatch):
        _create_legacy_membership(
            policy_setup["org"].id,
            policy_setup["internal_admin"].id,
            policy_setup["owner_role"].id,
            OrganizationMemberStatus.ACTIVE.value,
        )
        mock_query = MagicMock()
        monkeypatch.setattr(Product, "query", mock_query)

        with pytest.raises(ValueError):
            AccessService.get_organization_products(policy_setup["internal_admin"].id, policy_setup["org"].id)

        # A rejeição em get_active_membership deve interromper o fluxo
        # ANTES de AccessService chegar a consultar/montar a lista de
        # produtos - nunca calcula e descarta silenciosamente.
        mock_query.all.assert_not_called()

    def test_regular_user_still_gets_access_through_active_membership(self, policy_setup):
        OrganizationService.add_member(policy_setup["org"].id, policy_setup["regular_user"].id, "member")
        product = Product(code="policy-test-product-2", name="Produto Teste Politica 2", url="https://policy-test-2.local")
        db.session.add(product)
        db.session.flush()
        org_product = OrganizationProduct(
            organization_id=policy_setup["org"].id, product_id=product.id, status="active"
        )
        db.session.add(org_product)
        db.session.commit()

        items = AccessService.get_organization_products(policy_setup["regular_user"].id, policy_setup["org"].id)
        matching = [i for i in items if i["product"].id == product.id]
        assert matching and matching[0]["has_access"] is True


class TestInternalAdminRoutesRemainUnaffected:
    def test_internal_admin_can_still_access_admin_dashboard(self, client, app):
        with app.app_context():
            operator = User(
                name="Operador Dashboard",
                email="operador.dashboard.politica@example.com",
                is_internal_admin=True,
                email_verified_at=datetime.utcnow(),
            )
            operator.set_password(SYNTHETIC_PASSWORD)
            db.session.add(operator)
            db.session.commit()

        login_response = client.post(
            "/login",
            data={"email": "operador.dashboard.politica@example.com", "password": SYNTHETIC_PASSWORD},
        )
        assert login_response.status_code in (302, 303)

        response = client.get("/admin/")
        assert response.status_code == 200


class TestLastOwnerProtectionStillAppliesToRegularMembers:
    def test_cannot_remove_last_active_owner(self, policy_setup):
        org_id = policy_setup["org"].id
        user_id = policy_setup["regular_user"].id
        OrganizationService.add_member(org_id, user_id, "owner")

        with pytest.raises(ValueError, match="proprietário"):
            OrganizationService.remove_member(org_id, user_id)

        db.session.remove()
        member = OrganizationMember.query.filter_by(organization_id=org_id, user_id=user_id).first()
        assert member.status == OrganizationMemberStatus.ACTIVE.value


class TestGetUserOrganizationsExcludesInternalAdmin:
    def test_returns_empty_for_internal_admin_with_legacy_active_membership(self, policy_setup):
        org_id = policy_setup["org"].id
        admin_id = policy_setup["internal_admin"].id
        _create_legacy_membership(org_id, admin_id, policy_setup["owner_role"].id, OrganizationMemberStatus.ACTIVE.value)

        orgs = OrganizationService.get_user_organizations(admin_id)
        assert orgs == []

    def test_returns_empty_for_internal_admin_with_no_membership_at_all(self, policy_setup):
        orgs = OrganizationService.get_user_organizations(policy_setup["internal_admin"].id)
        assert orgs == []

    def test_regular_user_still_gets_their_organizations(self, policy_setup):
        org_id = policy_setup["org"].id
        user_id = policy_setup["regular_user"].id
        OrganizationService.add_member(org_id, user_id, "member")

        orgs = OrganizationService.get_user_organizations(user_id)
        assert len(orgs) == 1
        assert orgs[0].id == org_id


class TestDashboardRouteWithLegacyInternalAdminMembership:
    """Reproduz o cenário real que causaria HTTP 500: get_user_organizations
    (chamado por dashboard.index()) precisa excluir o administrador interno
    ANTES de dashboard.index() escolher current_org e chamar
    AccessService.get_organization_products - caso contrário, essa segunda
    chamada levantaria ValueError sem tratamento na rota."""

    def test_dashboard_responds_200_without_showing_legacy_org_or_products(self, client, app):
        with app.app_context():
            org = Organization(legal_name="Organizacao Legada Dashboard")
            db.session.add(org)
            db.session.flush()

            owner_role = Role(name="owner", description="Role owner")
            db.session.add(owner_role)
            db.session.flush()

            internal_admin = User(
                name="Admin Interno Dashboard Legado",
                email="admin.interno.dashboard.legado@example.com",
                is_internal_admin=True,
                email_verified_at=datetime.utcnow(),
            )
            internal_admin.set_password(SYNTHETIC_PASSWORD)
            db.session.add(internal_admin)
            db.session.flush()

            legacy_member = OrganizationMember(
                user_id=internal_admin.id,
                organization_id=org.id,
                role_id=owner_role.id,
                status=OrganizationMemberStatus.ACTIVE.value,
            )
            db.session.add(legacy_member)

            product = Product(
                code="dashboard-legacy-product", name="Produto Legado Dashboard", url="https://dashboard-legacy.local"
            )
            db.session.add(product)
            db.session.flush()
            org_product = OrganizationProduct(organization_id=org.id, product_id=product.id, status="active")
            db.session.add(org_product)

            db.session.commit()

        client.post(
            "/login",
            data={"email": "admin.interno.dashboard.legado@example.com", "password": SYNTHETIC_PASSWORD},
        )

        response = client.get("/")

        assert response.status_code == 200
        assert b"Organizacao Legada Dashboard" not in response.data
        assert b"Produto Legado Dashboard" not in response.data
        assert "Aguardando vincula".encode("utf-8") in response.data


class TestLastOwnerSanitizationFlowForLegacyInternalAdmin:
    """Item 6: um administrador interno legado que seja o único OWNER ativo
    não pode ser removido imediatamente (mesma proteção da Issue #15) -
    mas o saneamento continua possível assim que um OWNER comum legítimo é
    adicionado primeiro. Nenhuma alteração automática ocorre em nenhum dos
    passos."""

    def test_cannot_remove_legacy_internal_admin_if_it_is_the_only_active_owner(self, policy_setup):
        org_id = policy_setup["org"].id
        admin_id = policy_setup["internal_admin"].id
        legacy = _create_legacy_membership(
            org_id, admin_id, policy_setup["owner_role"].id, OrganizationMemberStatus.ACTIVE.value
        )
        legacy_id = legacy.id

        with pytest.raises(ValueError, match="proprietário"):
            OrganizationService.remove_member(org_id, admin_id)

        db.session.remove()
        reloaded = OrganizationMember.query.filter_by(id=legacy_id).first()
        assert reloaded.status == OrganizationMemberStatus.ACTIVE.value

    def test_can_remove_legacy_internal_admin_after_adding_a_real_owner(self, policy_setup):
        org_id = policy_setup["org"].id
        admin_id = policy_setup["internal_admin"].id
        regular_user_id = policy_setup["regular_user"].id
        owner_role_id = policy_setup["owner_role"].id
        legacy = _create_legacy_membership(org_id, admin_id, owner_role_id, OrganizationMemberStatus.ACTIVE.value)
        legacy_id = legacy.id

        # Passo 1: adiciona um OWNER comum legítimo - operação normal,
        # completamente independente desta política.
        OrganizationService.add_member(org_id, regular_user_id, "owner")

        # Passo 2: agora existem dois owners ativos, então o vínculo legado
        # do administrador interno pode ser removido sem violar a proteção
        # do último OWNER.
        OrganizationService.remove_member(org_id, admin_id)

        db.session.remove()
        reloaded_legacy = OrganizationMember.query.filter_by(id=legacy_id).first()
        assert reloaded_legacy.status == OrganizationMemberStatus.REMOVED.value

        real_owner = OrganizationMember.query.filter_by(
            organization_id=org_id, user_id=regular_user_id
        ).first()
        assert real_owner.status == OrganizationMemberStatus.ACTIVE.value
        assert real_owner.role_id == owner_role_id
