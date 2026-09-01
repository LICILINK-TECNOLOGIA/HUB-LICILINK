import importlib.util
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from app.extensions import db
from app.models import AuditLog, Organization, OrganizationMember, OrganizationProduct, Product, Role, User
from app.models.identity import OrganizationMemberStatus
from app.services.organization_service import OrganizationService
from app.services.access_service import AccessService
from app.services.audit_service import AuditService

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0a4802def021_add_status_to_organization_member.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("migration_0a4802def021", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMigrationBackfill:
    def test_migration_backfills_existing_memberships_as_active(self):
        # Testa upgrade()/downgrade() da migration isoladamente, contra um
        # SQLite em memória, simulando o esquema EXATAMENTE como estava
        # antes desta migration (sem a coluna status) e com vínculos
        # pré-existentes - sem tocar em nenhum banco real.
        migration = _load_migration_module()
        engine = sa.create_engine("sqlite:///:memory:")
        conn = engine.connect()
        # Inclui a UniqueConstraint (uq_org_member_user_org) já existente
        # antes desta migration, para provar que ela sobrevive intacta ao
        # upgrade() e ao downgrade().
        conn.execute(sa.text("""
            CREATE TABLE organization_members (
                id CHAR(32) NOT NULL PRIMARY KEY,
                user_id CHAR(32) NOT NULL,
                organization_id CHAR(32) NOT NULL,
                role_id CHAR(32) NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                CONSTRAINT uq_org_member_user_org UNIQUE (user_id, organization_id)
            )
        """))
        existing_ids = [uuid.uuid4().hex for _ in range(3)]
        existing_pairs = [(uuid.uuid4().hex, uuid.uuid4().hex) for _ in existing_ids]
        for member_id, (user_id_val, org_id_val) in zip(existing_ids, existing_pairs):
            conn.execute(
                sa.text(
                    "INSERT INTO organization_members "
                    "(id, user_id, organization_id, role_id, created_at, updated_at) "
                    "VALUES (:id, :u, :o, :r, :c, :up)"
                ),
                {
                    "id": member_id,
                    "u": user_id_val,
                    "o": org_id_val,
                    "r": uuid.uuid4().hex,
                    "c": "2026-01-01 00:00:00",
                    "up": "2026-01-01 00:00:00",
                },
            )
        conn.commit()
        first_user_id, first_org_id = existing_pairs[0]

        context = MigrationContext.configure(conn)
        with Operations.context(context):
            migration.upgrade()
        conn.commit()

        for member_id in existing_ids:
            row = conn.execute(
                sa.text("SELECT status FROM organization_members WHERE id=:id"), {"id": member_id}
            ).fetchone()
            assert row[0] == "active"

        # A UniqueConstraint pré-existente sobrevive ao upgrade (não foi
        # removida/recriada incorretamente pelo batch_alter_table): tentar
        # inserir o MESMO par (user_id, organization_id) de uma linha já
        # existente deve continuar sendo rejeitado.
        with pytest.raises(Exception):
            conn.execute(sa.text(
                "INSERT INTO organization_members "
                "(id, user_id, organization_id, role_id, created_at, updated_at) "
                "VALUES ('dup0000000000000000000000000000', :u, :o, 'r2222222222222222222222222222222', '2026-01-01', '2026-01-01')"
            ), {"u": first_user_id, "o": first_org_id})
            conn.commit()
        conn.rollback()

        # CHECK constraint rejeita valores inválidos de status.
        with pytest.raises(Exception):
            conn.execute(sa.text(
                "INSERT INTO organization_members "
                "(id, user_id, organization_id, role_id, created_at, updated_at, status) "
                "VALUES ('deadbeefdeadbeefdeadbeefdeadbeef', 'u', 'o', 'r', '2026-01-01', '2026-01-01', 'invalido')"
            ))
            conn.commit()

        conn.rollback()

        # downgrade() remove SOMENTE o que esta migration introduziu
        # (coluna status + CHECK constraint) - nunca a UniqueConstraint.
        with Operations.context(context):
            migration.downgrade()
        conn.commit()

        columns = [row[1] for row in conn.execute(sa.text("PRAGMA table_info(organization_members)"))]
        assert "status" not in columns

        # A UniqueConstraint continua ativa após o downgrade também.
        with pytest.raises(Exception):
            conn.execute(sa.text(
                "INSERT INTO organization_members "
                "(id, user_id, organization_id, role_id, created_at, updated_at) "
                "VALUES ('dup1111111111111111111111111111', :u, :o, 'r3333333333333333333333333333333', '2026-01-01', '2026-01-01')"
            ), {"u": first_user_id, "o": first_org_id})
            conn.commit()
        conn.rollback()


@pytest.fixture
def org_setup(app):
    with app.app_context():
        role = Role(name='owner', description='Role owner')
        member_role = Role(name='member', description='Role member')
        db.session.add_all([role, member_role])
        db.session.flush()

        org = Organization(legal_name='Organizacao Teste')
        db.session.add(org)
        db.session.flush()

        user = User(name='Usuario Teste', email='usuario.status@example.com')
        user.set_password('senha-sintetica-status-123')
        db.session.add(user)
        db.session.flush()

        db.session.commit()
        yield {'org': org, 'user': user, 'owner_role': role, 'member_role': member_role}


class TestNewMembershipDefault:
    def test_new_membership_receives_active_default(self, org_setup):
        member = OrganizationService.add_member(
            org_setup['org'].id, org_setup['user'].id, 'owner'
        )
        assert member.status == OrganizationMemberStatus.ACTIVE.value


class TestInvalidStatusRejected:
    def test_invalid_status_is_rejected(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        with pytest.raises(ValueError):
            OrganizationService.change_member_status(
                org_setup['org'].id, org_setup['user'].id, 'estado_inexistente'
            )


class TestAccessByStatus:
    def test_active_membership_grants_organization_access(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        orgs = OrganizationService.get_user_organizations(org_setup['user'].id)
        assert org_setup['org'] in orgs

    def test_suspended_membership_denies_access(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)
        orgs = OrganizationService.get_user_organizations(org_setup['user'].id)
        assert org_setup['org'] not in orgs

    def test_removed_membership_denies_access(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        OrganizationService.remove_member(org_setup['org'].id, org_setup['user'].id)
        orgs = OrganizationService.get_user_organizations(org_setup['user'].id)
        assert org_setup['org'] not in orgs


class TestSuspendedMembershipBlocksProductAccessEvenWithActiveContract:
    def test_suspended_membership_never_appears_as_accessible_org(self, org_setup):
        product = Product(code='gedo', name='L-GeDo', url='https://gedo.local')
        db.session.add(product)
        db.session.flush()

        org_product = OrganizationProduct(
            organization_id=org_setup['org'].id,
            product_id=product.id,
            status='active',
        )
        db.session.add(org_product)
        db.session.commit()

        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)

        # Mesmo com contrato 'active', a organização não deve aparecer para
        # este usuário, pois seu vínculo está suspenso.
        orgs = OrganizationService.get_user_organizations(org_setup['user'].id)
        assert org_setup['org'] not in orgs


class TestActiveOrgIdRevalidation:
    def test_get_active_membership_is_none_when_suspended(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)

        # Simula a revalidação de um `active_org_id` de sessão: deve
        # invalidar a seleção (retornar None) em vez de confiar no cache.
        result = OrganizationService.get_active_membership(org_setup['user'].id, org_setup['org'].id)
        assert result is None

    def test_get_active_membership_returns_member_when_active(self, org_setup):
        member = OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        result = OrganizationService.get_active_membership(org_setup['user'].id, org_setup['org'].id)
        assert result is not None
        assert result.id == member.id


class TestUserWithMultipleOrganizations:
    def test_user_keeps_access_only_to_active_organizations(self, org_setup):
        second_org = Organization(legal_name='Segunda Organizacao')
        db.session.add(second_org)
        db.session.commit()

        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        OrganizationService.add_member(second_org.id, org_setup['user'].id, 'member')
        OrganizationService.suspend_member(second_org.id, org_setup['user'].id)

        orgs = OrganizationService.get_user_organizations(org_setup['user'].id)
        assert org_setup['org'] in orgs
        assert second_org not in orgs


class TestAdminCanQueryAllStatuses:
    def test_admin_style_query_sees_suspended_and_removed(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)

        # Consulta "administrativa" (sem filtro de status) continua vendo o vínculo.
        all_memberships = OrganizationMember.query.filter_by(
            organization_id=org_setup['org'].id, user_id=org_setup['user'].id
        ).all()
        assert len(all_memberships) == 1
        assert all_memberships[0].status == OrganizationMemberStatus.SUSPENDED.value

        suspended = OrganizationMember.query.filter_by(status=OrganizationMemberStatus.SUSPENDED.value).all()
        assert len(suspended) == 1


class TestStatusChangeAudit:
    def test_status_change_creates_audit_log_entry(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        before_count = AuditLog.query.filter_by(action='organization.member.suspended').count()

        OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)

        logs = AuditLog.query.filter_by(action='organization.member.suspended').all()
        assert len(logs) == before_count + 1
        entry = logs[-1]
        assert entry.details['old_status'] == OrganizationMemberStatus.ACTIVE.value
        assert entry.details['new_status'] == OrganizationMemberStatus.SUSPENDED.value
        # Nunca CPF/CNPJ ou dados desnecessários - apenas identificadores internos.
        assert 'cpf' not in entry.details
        assert 'cnpj' not in entry.details


class TestStatusChangeRollback:
    def test_commit_failure_rolls_back_status_change(self, org_setup, monkeypatch):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')

        def _raise_commit(*args, **kwargs):
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(db.session, "commit", _raise_commit)

        with pytest.raises(ValueError):
            OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)

        monkeypatch.undo()
        member = OrganizationMember.query.filter_by(
            organization_id=org_setup['org'].id, user_id=org_setup['user'].id
        ).first()
        assert member.status == OrganizationMemberStatus.ACTIVE.value


class TestSuspensionPreservesRow:
    def test_suspend_does_not_delete_the_row(self, org_setup):
        member = OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        member_id = member.id

        OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)

        assert OrganizationMember.query.get(member_id) is not None

    def test_remove_does_not_delete_the_row(self, org_setup):
        member = OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        member_id = member.id

        OrganizationService.remove_member(org_setup['org'].id, org_setup['user'].id)

        assert OrganizationMember.query.get(member_id) is not None


class TestReactivationRestoresAccess:
    def test_reactivate_restores_organization_access(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)
        assert org_setup['org'] not in OrganizationService.get_user_organizations(org_setup['user'].id)

        OrganizationService.reactivate_member(org_setup['org'].id, org_setup['user'].id)
        assert org_setup['org'] in OrganizationService.get_user_organizations(org_setup['user'].id)


class TestNoUnrelatedChanges:
    def test_status_change_does_not_touch_role_or_contract_data(self, org_setup):
        product = Product(code='gedo2', name='L-GeDo 2', url='https://gedo2.local')
        db.session.add(product)
        db.session.flush()
        org_product = OrganizationProduct(
            organization_id=org_setup['org'].id, product_id=product.id, status='active'
        )
        db.session.add(org_product)
        db.session.commit()

        member = OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        original_role_id = member.role_id

        OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)

        db.session.refresh(member)
        db.session.refresh(org_product)
        assert member.role_id == original_role_id
        assert org_product.status == 'active'


@pytest.fixture
def product_setup(org_setup):
    product = Product(code='gedo-access', name='L-GeDo Access', url='https://gedo-access.local')
    db.session.add(product)
    db.session.flush()
    org_product = OrganizationProduct(
        organization_id=org_setup['org'].id, product_id=product.id, status='active'
    )
    db.session.add(org_product)
    db.session.commit()
    return {**org_setup, 'product': product, 'org_product': org_product}


class TestAccessServiceRequiresActiveMembership:
    """Testa AccessService.get_organization_products() diretamente, sem
    passar pelo dashboard - prova que o portão de vínculo ativo funciona no
    ponto central, não apenas em quem hoje o chama."""

    def test_active_membership_and_active_contract_grants_access(self, product_setup):
        OrganizationService.add_member(product_setup['org'].id, product_setup['user'].id, 'member')

        items = AccessService.get_organization_products(product_setup['user'].id, product_setup['org'].id)

        matching = [i for i in items if i['product'].id == product_setup['product'].id]
        assert matching and matching[0]['has_access'] is True

    def test_suspended_membership_denies_access_even_with_active_contract(self, product_setup):
        OrganizationService.add_member(product_setup['org'].id, product_setup['user'].id, 'member')
        OrganizationService.suspend_member(product_setup['org'].id, product_setup['user'].id)

        with pytest.raises(ValueError):
            AccessService.get_organization_products(product_setup['user'].id, product_setup['org'].id)

    def test_removed_membership_denies_access_even_with_active_contract(self, product_setup):
        OrganizationService.add_member(product_setup['org'].id, product_setup['user'].id, 'member')
        OrganizationService.remove_member(product_setup['org'].id, product_setup['user'].id)

        with pytest.raises(ValueError):
            AccessService.get_organization_products(product_setup['user'].id, product_setup['org'].id)

    def test_no_membership_denies_access_even_with_active_contract(self, product_setup):
        # Nenhum vínculo foi criado para este usuário.
        with pytest.raises(ValueError):
            AccessService.get_organization_products(product_setup['user'].id, product_setup['org'].id)

    def test_organization_id_from_another_organization_is_denied(self, product_setup):
        other_org = Organization(legal_name='Outra Organizacao')
        db.session.add(other_org)
        db.session.commit()

        # Usuário tem vínculo ativo com a organização ORIGINAL, mas não com
        # `other_org` - fornecer o organization_id de `other_org` (como um
        # chamador futuro poderia fazer via sessão/URL/formulário
        # adulterado) deve ser negado, mesmo que `other_org` também tenha
        # contrato ativo para o mesmo produto.
        OrganizationService.add_member(product_setup['org'].id, product_setup['user'].id, 'member')
        other_org_product = OrganizationProduct(
            organization_id=other_org.id, product_id=product_setup['product'].id, status='active'
        )
        db.session.add(other_org_product)
        db.session.commit()

        with pytest.raises(ValueError):
            AccessService.get_organization_products(product_setup['user'].id, other_org.id)

    def test_internal_admin_does_not_get_access_without_explicit_membership(self, product_setup):
        admin_user = User(name='Admin Interno', email='admin.interno@example.com', is_internal_admin=True)
        admin_user.set_password('senha-sintetica-admin-123')
        db.session.add(admin_user)
        db.session.commit()

        # Nenhuma regra explícita concede acesso de produto a administradores
        # internos por causa do flag is_internal_admin - eles precisam de
        # vínculo ativo como qualquer outra pessoa.
        with pytest.raises(ValueError):
            AccessService.get_organization_products(admin_user.id, product_setup['org'].id)


class TestStatusTransitionMatrix:
    def test_active_to_suspended_is_allowed(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        member = OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)
        assert member.status == OrganizationMemberStatus.SUSPENDED.value

    def test_suspended_to_active_is_allowed(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)
        member = OrganizationService.reactivate_member(org_setup['org'].id, org_setup['user'].id)
        assert member.status == OrganizationMemberStatus.ACTIVE.value

    def test_active_to_removed_is_allowed(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        member = OrganizationService.remove_member(org_setup['org'].id, org_setup['user'].id)
        assert member.status == OrganizationMemberStatus.REMOVED.value

    def test_suspended_to_removed_is_allowed(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)
        member = OrganizationService.remove_member(org_setup['org'].id, org_setup['user'].id)
        assert member.status == OrganizationMemberStatus.REMOVED.value

    def test_transition_to_same_state_is_rejected(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        with pytest.raises(ValueError):
            OrganizationService.change_member_status(
                org_setup['org'].id, org_setup['user'].id, OrganizationMemberStatus.ACTIVE.value
            )

        OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)
        with pytest.raises(ValueError):
            OrganizationService.change_member_status(
                org_setup['org'].id, org_setup['user'].id, OrganizationMemberStatus.SUSPENDED.value
            )

    def test_removed_is_terminal_via_generic_change_member_status(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        OrganizationService.remove_member(org_setup['org'].id, org_setup['user'].id)

        # removed -> active via change_member_status genérico: rejeitado.
        with pytest.raises(ValueError):
            OrganizationService.change_member_status(
                org_setup['org'].id, org_setup['user'].id, OrganizationMemberStatus.ACTIVE.value
            )

    def test_removed_to_suspended_is_rejected(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        OrganizationService.remove_member(org_setup['org'].id, org_setup['user'].id)

        with pytest.raises(ValueError):
            OrganizationService.change_member_status(
                org_setup['org'].id, org_setup['user'].id, OrganizationMemberStatus.SUSPENDED.value
            )

    def test_reactivate_member_does_not_work_on_removed_membership(self, org_setup):
        # reactivate_member é exclusivamente suspended->active; nunca deve
        # reverter silenciosamente um vínculo removido.
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        OrganizationService.remove_member(org_setup['org'].id, org_setup['user'].id)

        with pytest.raises(ValueError):
            OrganizationService.reactivate_member(org_setup['org'].id, org_setup['user'].id)

    def test_unknown_status_value_is_rejected(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        with pytest.raises(ValueError):
            OrganizationService.change_member_status(
                org_setup['org'].id, org_setup['user'].id, 'estado_desconhecido'
            )

    def test_restore_removed_member_reverts_to_active(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        OrganizationService.remove_member(org_setup['org'].id, org_setup['user'].id)

        member = OrganizationService.restore_removed_member(org_setup['org'].id, org_setup['user'].id)
        assert member.status == OrganizationMemberStatus.ACTIVE.value

    def test_restore_removed_member_rejects_non_removed_membership(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        # Vínculo está 'active', não 'removed'.
        with pytest.raises(ValueError):
            OrganizationService.restore_removed_member(org_setup['org'].id, org_setup['user'].id)


class TestRemovedMembershipReentry:
    def test_add_member_on_removed_membership_gives_clear_restoration_error(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        OrganizationService.remove_member(org_setup['org'].id, org_setup['user'].id)

        before_count = OrganizationMember.query.filter_by(
            organization_id=org_setup['org'].id, user_id=org_setup['user'].id
        ).count()

        with pytest.raises(ValueError, match="restauração"):
            OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')

        # Nenhuma linha duplicada foi criada (constraint de unicidade preservada).
        after_count = OrganizationMember.query.filter_by(
            organization_id=org_setup['org'].id, user_id=org_setup['user'].id
        ).count()
        assert after_count == before_count == 1

        # O status permanece 'removed' - não foi reativado silenciosamente.
        member = OrganizationMember.query.filter_by(
            organization_id=org_setup['org'].id, user_id=org_setup['user'].id
        ).first()
        assert member.status == OrganizationMemberStatus.REMOVED.value


class TestLastActiveOwnerProtection:
    def _add_owner(self, org, user, email_suffix=""):
        if email_suffix:
            user = User(name=f'Owner {email_suffix}', email=f'owner{email_suffix}@example.com')
            user.set_password('senha-sintetica-owner-123')
            db.session.add(user)
            db.session.commit()
        return OrganizationService.add_member(org.id, user.id, 'owner'), user

    def test_cannot_suspend_last_active_owner(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'owner')
        with pytest.raises(ValueError):
            OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)

        member = OrganizationMember.query.filter_by(
            organization_id=org_setup['org'].id, user_id=org_setup['user'].id
        ).first()
        assert member.status == OrganizationMemberStatus.ACTIVE.value

    def test_cannot_remove_last_active_owner(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'owner')
        with pytest.raises(ValueError):
            OrganizationService.remove_member(org_setup['org'].id, org_setup['user'].id)

        member = OrganizationMember.query.filter_by(
            organization_id=org_setup['org'].id, user_id=org_setup['user'].id
        ).first()
        assert member.status == OrganizationMemberStatus.ACTIVE.value

    def test_suspended_owner_does_not_count_as_active(self, org_setup):
        # Dois owners; suspende um (ainda resta 1 ativo, permitido); tentar
        # suspender o segundo (o único ativo restante) deve falhar, porque o
        # primeiro (suspenso) não conta mais como owner ativo.
        member1, user1 = self._add_owner(org_setup['org'], org_setup['user'])
        _, user2 = self._add_owner(org_setup['org'], None, email_suffix="2")

        OrganizationService.suspend_member(org_setup['org'].id, user1.id)

        with pytest.raises(ValueError):
            OrganizationService.suspend_member(org_setup['org'].id, user2.id)

    def test_two_active_owners_one_can_be_suspended(self, org_setup):
        _, user1 = self._add_owner(org_setup['org'], org_setup['user'])
        _, user2 = self._add_owner(org_setup['org'], None, email_suffix="2b")

        member = OrganizationService.suspend_member(org_setup['org'].id, user1.id)
        assert member.status == OrganizationMemberStatus.SUSPENDED.value

        # O segundo owner continua ativo.
        remaining = OrganizationMember.query.filter_by(
            organization_id=org_setup['org'].id, user_id=user2.id
        ).first()
        assert remaining.status == OrganizationMemberStatus.ACTIVE.value

    def test_reactivation_does_not_change_role(self, org_setup):
        member, _ = self._add_owner(org_setup['org'], org_setup['user'])
        _, user2 = self._add_owner(org_setup['org'], None, email_suffix="3")
        original_role_id = member.role_id

        OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)
        OrganizationService.reactivate_member(org_setup['org'].id, org_setup['user'].id)

        db.session.refresh(member)
        assert member.role_id == original_role_id

    def test_restoration_does_not_change_role(self, org_setup):
        member, _ = self._add_owner(org_setup['org'], org_setup['user'])
        _, user2 = self._add_owner(org_setup['org'], None, email_suffix="4")
        original_role_id = member.role_id

        OrganizationService.remove_member(org_setup['org'].id, org_setup['user'].id)
        OrganizationService.restore_removed_member(org_setup['org'].id, org_setup['user'].id)

        db.session.refresh(member)
        assert member.role_id == original_role_id

    def test_owner_protection_failure_leaves_no_partial_state_or_audit_log(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'owner')
        audit_count_before = AuditLog.query.count()

        with pytest.raises(ValueError):
            OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)

        member = OrganizationMember.query.filter_by(
            organization_id=org_setup['org'].id, user_id=org_setup['user'].id
        ).first()
        assert member.status == OrganizationMemberStatus.ACTIVE.value
        assert AuditLog.query.count() == audit_count_before


class TestStatusAndAuditAreAtomic:
    def test_single_commit_covers_status_and_audit_log(self, org_setup, monkeypatch):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')

        original_commit = db.session.commit
        call_count = {"n": 0}

        def _counting_commit():
            call_count["n"] += 1
            return original_commit()

        monkeypatch.setattr(db.session, "commit", _counting_commit)

        OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)

        # Uma única chamada de commit cobre a mudança de status E a entrada
        # de auditoria - prova estrutural de atomicidade (mesma transação).
        assert call_count["n"] == 1

    def test_commit_failure_leaves_neither_status_nor_audit_log(self, org_setup, monkeypatch):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        audit_count_before = AuditLog.query.count()

        def _raise_commit(*args, **kwargs):
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(db.session, "commit", _raise_commit)

        with pytest.raises(ValueError):
            OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)

        monkeypatch.undo()
        member = OrganizationMember.query.filter_by(
            organization_id=org_setup['org'].id, user_id=org_setup['user'].id
        ).first()
        assert member.status == OrganizationMemberStatus.ACTIVE.value
        assert AuditLog.query.count() == audit_count_before


class TestResourceIdUuidCompatibility:
    """Regressão do bug corrigido nesta Issue: AuditService.log_action()
    recebia resource_id=str(uuid_obj) em create_organization, add_member,
    change_member_role e na transição de status - o que quebrava contra a
    coluna UUID(as_uuid=True) de AuditLog. Este teste falharia (com
    AttributeError/StatementError) com a versão antiga do código."""

    def test_add_member_audit_log_resource_id_is_real_uuid_not_string(self, org_setup):
        member = OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')

        log = AuditLog.query.filter_by(action='organization.member.added').order_by(
            AuditLog.created_at.desc()
        ).first()
        assert log is not None
        assert isinstance(log.resource_id, uuid.UUID)
        assert log.resource_id == org_setup['org'].id

    def test_status_change_audit_log_resource_id_is_real_uuid_not_string(self, org_setup):
        member = OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)

        log = AuditLog.query.filter_by(action='organization.member.suspended').order_by(
            AuditLog.created_at.desc()
        ).first()
        assert log is not None
        assert isinstance(log.resource_id, uuid.UUID)
        assert log.resource_id == member.id


class TestOwnerInvariantLocking:
    """Verifica que o lock da linha de Organization é solicitado antes da
    contagem/alteração em todo caminho que participa da invariante "ao
    menos um OWNER ativo". Não reproduz bloqueio real de concorrência
    (SQLite não implementa row-level locking como o PostgreSQL) - apenas
    confirma que `with_for_update()` é de fato requisitado. Validação de
    bloqueio real sob concorrência fica para homologação com PostgreSQL
    (ver nota no serviço)."""

    def _add_second_owner(self, org, suffix):
        user = User(name=f'Owner {suffix}', email=f'owner.lock.{suffix}@example.com')
        user.set_password('senha-sintetica-lock-123')
        db.session.add(user)
        db.session.commit()
        OrganizationService.add_member(org.id, user.id, 'owner')
        return user

    def test_suspend_owner_acquires_organization_lock_before_count(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'owner')
        self._add_second_owner(org_setup['org'], 'suspend')

        with patch.object(
            OrganizationService, "_lock_organization_row",
            wraps=OrganizationService._lock_organization_row,
        ) as spy_lock:
            OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)
            spy_lock.assert_called_once_with(org_setup['org'].id)

    def test_remove_owner_acquires_organization_lock_before_count(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'owner')
        self._add_second_owner(org_setup['org'], 'remove')

        with patch.object(
            OrganizationService, "_lock_organization_row",
            wraps=OrganizationService._lock_organization_row,
        ) as spy_lock:
            OrganizationService.remove_member(org_setup['org'].id, org_setup['user'].id)
            spy_lock.assert_called_once_with(org_setup['org'].id)

    def test_change_role_away_from_owner_acquires_organization_lock(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'owner')
        self._add_second_owner(org_setup['org'], 'role')

        with patch.object(
            OrganizationService, "_lock_organization_row",
            wraps=OrganizationService._lock_organization_row,
        ) as spy_lock:
            OrganizationService.change_member_role(org_setup['org'].id, org_setup['user'].id, 'member')
            spy_lock.assert_called_once_with(org_setup['org'].id)

    def test_restore_removed_owner_acquires_organization_lock(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'owner')
        self._add_second_owner(org_setup['org'], 'restore')
        OrganizationService.remove_member(org_setup['org'].id, org_setup['user'].id)

        with patch.object(
            OrganizationService, "_lock_organization_row",
            wraps=OrganizationService._lock_organization_row,
        ) as spy_lock:
            OrganizationService.restore_removed_member(org_setup['org'].id, org_setup['user'].id)
            spy_lock.assert_called_once_with(org_setup['org'].id)

    def test_reactivate_suspended_owner_acquires_organization_lock(self, org_setup):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'owner')
        self._add_second_owner(org_setup['org'], 'reactivate')
        OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)

        with patch.object(
            OrganizationService, "_lock_organization_row",
            wraps=OrganizationService._lock_organization_row,
        ) as spy_lock:
            OrganizationService.reactivate_member(org_setup['org'].id, org_setup['user'].id)
            spy_lock.assert_called_once_with(org_setup['org'].id)

    def test_lock_is_requested_before_commit(self, org_setup):
        # Confirma a ORDEM em termos observáveis e robustos: o lock precisa
        # ter sido solicitado ANTES do commit que persiste a mudança -
        # provando que ele é adquirido durante a fase de leitura/validação
        # (contagem de owners), não depois. A implementação (ver
        # `_apply_status_transition`) já garante isso por construção: o
        # lock é a primeira instrução do bloco protegido.
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'owner')
        self._add_second_owner(org_setup['org'], 'order')

        call_order = []
        real_lock = OrganizationService._lock_organization_row
        original_commit = db.session.commit

        def _tracking_lock(organization_id):
            call_order.append('lock')
            return real_lock(organization_id)

        def _tracking_commit():
            call_order.append('commit')
            return original_commit()

        with patch.object(OrganizationService, "_lock_organization_row", side_effect=_tracking_lock):
            with patch.object(db.session, "commit", side_effect=_tracking_commit):
                OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)

        assert call_order == ['lock', 'commit']


class TestAuditServiceCommitFalseContract:
    def test_log_action_with_commit_false_neither_commits_nor_rolls_back(self, org_setup, monkeypatch):
        commit_calls = {"n": 0}
        rollback_calls = {"n": 0}
        original_commit = db.session.commit
        original_rollback = db.session.rollback

        def _counting_commit():
            commit_calls["n"] += 1
            return original_commit()

        def _counting_rollback():
            rollback_calls["n"] += 1
            return original_rollback()

        monkeypatch.setattr(db.session, "commit", _counting_commit)
        monkeypatch.setattr(db.session, "rollback", _counting_rollback)

        AuditService.log_action(action='test.commit_false.contract', commit=False)

        assert commit_calls["n"] == 0
        assert rollback_calls["n"] == 0

        # A entrada foi adicionada à sessão (pendente) - só persiste quando o
        # CHAMADOR decide commitar.
        db.session.commit()
        assert AuditLog.query.filter_by(action='test.commit_false.contract').count() == 1


class TestAuditLogFailureAtomicity:
    def test_status_change_rolls_back_if_audit_log_action_raises(self, org_setup, monkeypatch):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        audit_count_before = AuditLog.query.count()

        def _raise_log_action(*args, **kwargs):
            raise RuntimeError("synthetic audit failure")

        monkeypatch.setattr(AuditService, "log_action", _raise_log_action)

        with pytest.raises(ValueError):
            OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)

        monkeypatch.undo()
        member = OrganizationMember.query.filter_by(
            organization_id=org_setup['org'].id, user_id=org_setup['user'].id
        ).first()
        assert member.status == OrganizationMemberStatus.ACTIVE.value
        assert AuditLog.query.count() == audit_count_before

    def test_role_change_rolls_back_if_audit_log_action_raises(self, org_setup):
        member = OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        audit_count_before = AuditLog.query.count()
        original_role_id = member.role_id

        def _raise_log_action(*args, **kwargs):
            raise RuntimeError("synthetic audit failure")

        with patch.object(AuditService, "log_action", side_effect=_raise_log_action):
            with pytest.raises(ValueError):
                OrganizationService.change_member_role(org_setup['org'].id, org_setup['user'].id, 'owner')

        db.session.refresh(member)
        assert member.role_id == original_role_id
        assert AuditLog.query.count() == audit_count_before

    def test_exactly_one_commit_on_successful_status_change(self, org_setup, monkeypatch):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        original_commit = db.session.commit
        call_count = {"n": 0}

        def _counting_commit():
            call_count["n"] += 1
            return original_commit()

        monkeypatch.setattr(db.session, "commit", _counting_commit)

        OrganizationService.suspend_member(org_setup['org'].id, org_setup['user'].id)
        assert call_count["n"] == 1

    def test_exactly_one_commit_on_successful_role_change(self, org_setup, monkeypatch):
        OrganizationService.add_member(org_setup['org'].id, org_setup['user'].id, 'member')
        original_commit = db.session.commit
        call_count = {"n": 0}

        def _counting_commit():
            call_count["n"] += 1
            return original_commit()

        monkeypatch.setattr(db.session, "commit", _counting_commit)

        OrganizationService.change_member_role(org_setup['org'].id, org_setup['user'].id, 'owner')
        assert call_count["n"] == 1


class TestDashboardRouteDoesNotLeakValueError:
    def test_authenticated_user_with_active_membership_gets_200_not_500(self, client, app, get_csrf_token):
        with app.app_context():
            org = Organization(legal_name='Organizacao Dashboard Teste')
            db.session.add(org)
            db.session.flush()

            user = User(
                name='Usuario Dashboard',
                email='usuario.dashboard.500@example.com',
                email_verified_at=datetime.utcnow(),
            )
            user.set_password('senha-sintetica-dashboard-123')
            db.session.add(user)
            db.session.flush()

            product = Product(
                code='dashboard-product-500', name='Produto Dashboard',
                url='https://dashboard-product-500.local',
            )
            db.session.add(product)
            db.session.flush()

            org_product = OrganizationProduct(
                organization_id=org.id, product_id=product.id, status='active'
            )
            db.session.add(org_product)
            db.session.commit()

            OrganizationService.add_member(org.id, user.id, 'member')

        login_response = client.post('/login', data={
            'email': 'usuario.dashboard.500@example.com',
            'password': 'senha-sintetica-dashboard-123',
            'csrf_token': get_csrf_token(client),
        })
        assert login_response.status_code == 302

        # Fluxo normal (organização obtida via get_user_organizations, já
        # filtrada para vínculo ativo): nunca deve propagar ValueError nem
        # resultar em erro 500.
        dashboard_response = client.get('/')
        assert dashboard_response.status_code == 200


class TestDashboardDisplaysOrganizationLegalName:
    """Issue #32: o dashboard deve exibir `Organization.legal_name` - o
    atributo real do model desde a migration b0a61c555d44 - e nunca o
    atributo `name`, que não existe mais em `Organization` (renderizaria
    como string vazia via Undefined do Jinja, sem erro)."""

    SYNTHETIC_PASSWORD = 'senha-sintetica-issue-32-123'

    def _create_linked_user(self, legal_name, email):
        org = Organization(legal_name=legal_name)
        db.session.add(org)
        db.session.flush()

        user = User(
            name='Usuario Issue 32',
            email=email,
            email_verified_at=datetime.utcnow(),
        )
        user.set_password(self.SYNTHETIC_PASSWORD)
        db.session.add(user)
        db.session.flush()

        product = Product(
            code=f'produto-issue-32-{uuid.uuid4().hex[:8]}',
            name='Produto Issue 32',
            url='https://produto-issue-32.local',
        )
        db.session.add(product)
        db.session.flush()

        org_product = OrganizationProduct(organization_id=org.id, product_id=product.id, status='active')
        db.session.add(org_product)
        db.session.commit()

        OrganizationService.add_member(org.id, user.id, 'member')
        return org, user, product

    def _login(self, client, email, get_csrf_token):
        return client.post('/login', data={
            'email': email,
            'password': self.SYNTHETIC_PASSWORD,
            'csrf_token': get_csrf_token(client),
        })

    def test_active_member_gets_200_and_legal_name_rendered_non_empty(self, client, app, get_csrf_token):
        with app.app_context():
            self._create_linked_user('Organizacao Issue 32 LTDA', 'usuario.issue32.basico@example.com')

        self._login(client, 'usuario.issue32.basico@example.com', get_csrf_token)
        response = client.get('/')

        assert response.status_code == 200
        html = response.data.decode('utf-8')
        assert 'Organizacao Issue 32 LTDA' in html
        # Regressão: `org.name` (inexistente) renderiza como Undefined do
        # Jinja, isto é, string vazia, sem erro - o bloco não pode ficar
        # vazio.
        assert '<strong></strong>' not in html

    def test_accented_legal_name_is_displayed_correctly(self, client, app, get_csrf_token):
        with app.app_context():
            self._create_linked_user('Organização São João Ltda', 'usuario.issue32.acentos@example.com')

        self._login(client, 'usuario.issue32.acentos@example.com', get_csrf_token)
        response = client.get('/')

        html = response.data.decode('utf-8')
        assert 'Organização São João Ltda' in html

    def test_html_characters_in_legal_name_are_escaped_not_interpreted(self, client, app, get_csrf_token):
        dangerous_name = 'Nome <script>alert(1)</script> & "Cia"'
        with app.app_context():
            self._create_linked_user(dangerous_name, 'usuario.issue32.escaping@example.com')

        self._login(client, 'usuario.issue32.escaping@example.com', get_csrf_token)
        response = client.get('/')

        html = response.data.decode('utf-8')
        assert '<script>alert(1)</script>' not in html
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html
        assert '&amp;' in html

    def test_unlinked_user_does_not_see_any_organization(self, client, app, get_csrf_token):
        with app.app_context():
            org = Organization(legal_name='Organizacao Sem Vinculo Issue 32')
            db.session.add(org)
            db.session.flush()

            user = User(
                name='Usuario Sem Vinculo Issue 32',
                email='usuario.issue32.semvinculo@example.com',
                email_verified_at=datetime.utcnow(),
            )
            user.set_password(self.SYNTHETIC_PASSWORD)
            db.session.add(user)
            db.session.commit()

        self._login(client, 'usuario.issue32.semvinculo@example.com', get_csrf_token)
        response = client.get('/')

        assert response.status_code == 200
        html = response.data.decode('utf-8')
        assert 'Organizacao Sem Vinculo Issue 32' not in html
        assert 'Aguardando vincula' in html

    def test_internal_admin_without_membership_does_not_receive_client_organization(self, client, app, get_csrf_token):
        with app.app_context():
            org = Organization(legal_name='Organizacao Cliente Issue 32 Admin')
            db.session.add(org)

            internal_admin = User(
                name='Admin Interno Issue 32',
                email='admin.interno.issue32@example.com',
                is_internal_admin=True,
                email_verified_at=datetime.utcnow(),
            )
            internal_admin.set_password(self.SYNTHETIC_PASSWORD)
            db.session.add(internal_admin)
            db.session.commit()

        self._login(client, 'admin.interno.issue32@example.com', get_csrf_token)
        response = client.get('/')

        assert response.status_code == 200
        html = response.data.decode('utf-8')
        assert 'Organizacao Cliente Issue 32 Admin' not in html
        assert 'Aguardando vincula' in html

    def test_organization_model_has_no_dynamic_name_attribute(self, app):
        # Prova direta de que o template não pode depender de `org.name`:
        # o atributo simplesmente não existe no model, desde a migration
        # b0a61c555d44 (drop_column('name')).
        with app.app_context():
            org = Organization(legal_name='Organizacao Sem Atributo Name Issue 32')
            db.session.add(org)
            db.session.commit()
            assert not hasattr(org, 'name')

    def test_permissions_and_product_listing_remain_unaffected(self, client, app, get_csrf_token):
        with app.app_context():
            self._create_linked_user('Organizacao Produtos Issue 32', 'usuario.issue32.produtos@example.com')

        self._login(client, 'usuario.issue32.produtos@example.com', get_csrf_token)
        response = client.get('/')

        assert response.status_code == 200
        html = response.data.decode('utf-8')
        # A correção não alterou AccessService/OrganizationService: a
        # listagem de produtos e o status de acesso continuam corretos.
        assert 'Produto Issue 32' in html
        assert 'status-active' in html
        assert 'Acessar Sistema' in html
