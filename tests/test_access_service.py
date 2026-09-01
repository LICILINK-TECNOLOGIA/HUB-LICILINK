import uuid
from datetime import datetime

import pytest

from app.extensions import db
from app.models import AuditLog, Organization, OrganizationProduct, Product, User
from app.services.access_service import (
    AccessService,
    ProductAccessError,
    ProductAccessOperationError,
)
from app.services.bootstrap_service import BootstrapService, STRUCTURAL_PRODUCTS
from app.services.organization_service import OrganizationService

SYNTHETIC_PASSWORD = "senha-sintetica-issue-30-123"


def _create_organization(legal_name="Organizacao Issue 30"):
    org = Organization(legal_name=legal_name)
    db.session.add(org)
    db.session.commit()
    return org


def _create_actor(email="admin.ator.issue30@example.com"):
    user = User(
        name="Admin Ator Issue 30",
        email=email,
        is_internal_admin=True,
        email_verified_at=datetime.utcnow(),
    )
    user.set_password(SYNTHETIC_PASSWORD)
    db.session.add(user)
    db.session.commit()
    return user


class TestListOrganizationProductsForAdmin:
    def test_contains_kalender_gedo_and_hunt_in_canonical_order(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()

            items = AccessService.list_organization_products_for_admin(org.id)

            codes = [item["product"].code for item in items]
            assert codes == ["kalender", "gedo", "hunt"]

    def test_does_not_require_active_membership(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            # Nenhum OrganizationMember criado - admin interno nunca tem
            # vínculo com a organização cliente (Issue #19).
            items = AccessService.list_organization_products_for_admin(org.id)
            assert len(items) == 3

    def test_unlinked_organization_shows_all_three_as_unsubscribed(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()

            items = AccessService.list_organization_products_for_admin(org.id)

            for item in items:
                assert item["status"] == "unsubscribed"
                assert item["has_access"] is False

    def test_nonexistent_organization_raises_value_error(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            with pytest.raises(ValueError):
                AccessService.list_organization_products_for_admin(uuid.uuid4())

    def test_does_not_create_or_modify_any_record(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()

            before = OrganizationProduct.query.count()
            AccessService.list_organization_products_for_admin(org.id)
            after = OrganizationProduct.query.count()

            assert before == after == 0

    def test_inactive_and_suspended_are_reported_as_no_access(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            product = Product.query.filter_by(code="gedo").first()
            db.session.add(OrganizationProduct(organization_id=org.id, product_id=product.id, status="suspended"))
            db.session.commit()

            items = AccessService.list_organization_products_for_admin(org.id)
            by_code = {item["product"].code: item for item in items}

            assert by_code["gedo"]["status"] == "suspended"
            assert by_code["gedo"]["has_access"] is False


class TestGrantProductAccess:
    @pytest.mark.parametrize("code", ["kalender", "gedo", "hunt"])
    def test_grant_sets_active(self, app, code):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            actor = _create_actor()

            result = AccessService.grant_product_access(org.id, code, actor_user_id=actor.id)

            assert result.changed is True
            assert result.organization_product.status == "active"
            assert result.organization_product.organization_id == org.id

    def test_grant_creates_audit_log_with_correct_fields(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            actor = _create_actor()

            result = AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)

            log = AuditLog.query.filter_by(action="organization.product.granted").first()
            assert log is not None
            assert log.user_id == actor.id
            assert log.organization_id == org.id
            assert log.resource_type == "organization_product"
            assert log.resource_id == result.organization_product.id
            assert log.details["product_code"] == "gedo"
            assert log.details["old_status"] is None
            assert log.details["new_status"] == "active"

    def test_repeated_grant_does_not_duplicate_the_row(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            actor = _create_actor()

            AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)
            AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)

            assert OrganizationProduct.query.filter_by(organization_id=org.id).count() == 1

    def test_repeated_grant_does_not_create_second_audit_log(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            actor = _create_actor()

            AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)
            AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)

            assert AuditLog.query.filter_by(action="organization.product.granted").count() == 1

    def test_repeated_grant_is_an_idempotent_no_op(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            actor = _create_actor()

            AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)
            result = AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)

            assert result.changed is False
            assert result.organization_product.status == "active"

    def test_repeated_grant_does_not_change_updated_at(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            actor = _create_actor()

            first = AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)
            original_updated_at = first.organization_product.updated_at
            organization_id = org.id

            AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)

            db.session.remove()
            reloaded = OrganizationProduct.query.filter_by(organization_id=organization_id).first()
            assert reloaded.updated_at == original_updated_at


class TestRevokeProductAccess:
    def test_revoke_active_link_sets_inactive(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            actor = _create_actor()
            AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)

            result = AccessService.revoke_product_access(org.id, "gedo", actor_user_id=actor.id)

            assert result.changed is True
            assert result.organization_product.status == "inactive"

    def test_revoke_preserves_the_same_row(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            actor = _create_actor()
            granted = AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)
            original_id = granted.organization_product.id

            AccessService.revoke_product_access(org.id, "gedo", actor_user_id=actor.id)

            assert OrganizationProduct.query.filter_by(organization_id=org.id).count() == 1
            remaining = OrganizationProduct.query.filter_by(organization_id=org.id).first()
            assert remaining.id == original_id

    def test_revoke_creates_audit_log_with_correct_fields(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            actor = _create_actor()
            AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)

            result = AccessService.revoke_product_access(org.id, "gedo", actor_user_id=actor.id)

            log = AuditLog.query.filter_by(action="organization.product.revoked").first()
            assert log is not None
            assert log.user_id == actor.id
            assert log.organization_id == org.id
            assert log.resource_id == result.organization_product.id
            assert log.details["product_code"] == "gedo"
            assert log.details["old_status"] == "active"
            assert log.details["new_status"] == "inactive"

    def test_repeated_revoke_is_no_op(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            actor = _create_actor()
            AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)
            AccessService.revoke_product_access(org.id, "gedo", actor_user_id=actor.id)

            result = AccessService.revoke_product_access(org.id, "gedo", actor_user_id=actor.id)

            assert result.changed is False
            assert result.organization_product.status == "inactive"

    def test_repeated_revoke_does_not_create_new_audit_log(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            actor = _create_actor()
            AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)
            AccessService.revoke_product_access(org.id, "gedo", actor_user_id=actor.id)

            AccessService.revoke_product_access(org.id, "gedo", actor_user_id=actor.id)

            assert AuditLog.query.filter_by(action="organization.product.revoked").count() == 1

    def test_revoke_nonexistent_link_does_not_create_row_or_audit_log(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            actor = _create_actor()

            result = AccessService.revoke_product_access(org.id, "kalender", actor_user_id=actor.id)

            assert result.changed is False
            assert result.organization_product is None
            assert OrganizationProduct.query.filter_by(organization_id=org.id).count() == 0
            assert AuditLog.query.filter_by(action="organization.product.revoked").count() == 0

    def test_reactivation_preserves_the_same_organization_product_id(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            actor = _create_actor()

            granted = AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)
            original_id = granted.organization_product.id
            AccessService.revoke_product_access(org.id, "gedo", actor_user_id=actor.id)
            regranted = AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)

            assert regranted.organization_product.id == original_id
            assert regranted.changed is True
            assert regranted.organization_product.status == "active"
            assert OrganizationProduct.query.filter_by(organization_id=org.id).count() == 1

    def test_reactivation_creates_audit_log_from_inactive_to_active(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            actor = _create_actor()

            AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)
            AccessService.revoke_product_access(org.id, "gedo", actor_user_id=actor.id)
            AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)

            logs = AuditLog.query.filter_by(action="organization.product.granted").order_by(AuditLog.created_at).all()
            assert len(logs) == 2
            assert logs[-1].details["old_status"] == "inactive"
            assert logs[-1].details["new_status"] == "active"


class TestIsolation:
    def test_grant_to_gedo_does_not_grant_kalender_or_hunt(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            actor = _create_actor()

            AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)

            items = AccessService.list_organization_products_for_admin(org.id)
            by_code = {item["product"].code: item for item in items}
            assert by_code["gedo"]["has_access"] is True
            assert by_code["kalender"]["has_access"] is False
            assert by_code["hunt"]["has_access"] is False

    def test_grant_to_one_organization_does_not_affect_another(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_a = _create_organization("Organizacao A Issue 30")
            org_b = _create_organization("Organizacao B Issue 30")
            actor = _create_actor()

            AccessService.grant_product_access(org_a.id, "kalender", actor_user_id=actor.id)

            items_b = AccessService.list_organization_products_for_admin(org_b.id)
            by_code_b = {item["product"].code: item for item in items_b}
            assert by_code_b["kalender"]["has_access"] is False

    def test_existing_grants_remain_intact_after_unrelated_operation(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            actor = _create_actor()

            pre_existing = AccessService.grant_product_access(org.id, "kalender", actor_user_id=actor.id)
            pre_existing_id = pre_existing.organization_product.id

            # Operação totalmente separada, em outro produto.
            AccessService.grant_product_access(org.id, "hunt", actor_user_id=actor.id)
            AccessService.revoke_product_access(org.id, "hunt", actor_user_id=actor.id)

            db.session.remove()
            reloaded = OrganizationProduct.query.filter_by(id=pre_existing_id).first()
            assert reloaded is not None
            assert reloaded.status == "active"


class TestValidation:
    def test_nonexistent_organization_is_rejected(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            actor = _create_actor()

            with pytest.raises(ValueError):
                AccessService.grant_product_access(uuid.uuid4(), "kalender", actor_user_id=actor.id)

    def test_non_canonical_code_is_rejected_even_if_a_product_row_exists(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            actor = _create_actor()

            # Produto fora do catálogo canônico, criado manualmente (fora
            # do caminho oficial de bootstrap) - nunca deve ser aceito.
            rogue = Product(code="l-kalender", name="Produto Nao Canonico", url="https://rogue.example")
            db.session.add(rogue)
            db.session.commit()

            with pytest.raises(ProductAccessError):
                AccessService.grant_product_access(org.id, "l-kalender", actor_user_id=actor.id)
            assert OrganizationProduct.query.filter_by(organization_id=org.id).count() == 0

    def test_canonical_code_missing_from_database_is_rejected_safely(self, app):
        with app.app_context():
            # Bootstrap NÃO executado - nenhum Product existe ainda.
            org = _create_organization()
            actor = _create_actor()

            with pytest.raises(ProductAccessError):
                AccessService.grant_product_access(org.id, "kalender", actor_user_id=actor.id)
            assert OrganizationProduct.query.count() == 0


class TestAuditLogSafety:
    def test_audit_log_details_never_contain_secrets(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            actor = _create_actor()

            AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)
            AccessService.revoke_product_access(org.id, "gedo", actor_user_id=actor.id)

            logs = AuditLog.query.filter(AuditLog.action.like("organization.product.%")).all()
            assert len(logs) == 2
            forbidden = ("senha", "password", "token", "cookie", "csrf", "api_key", "authorization")
            for log in logs:
                serialized = str(log.details).lower()
                for word in forbidden:
                    assert word not in serialized


class TestAtomicity:
    def test_commit_failure_rolls_back_everything(self, app, monkeypatch):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            actor = _create_actor()

            def _raise_commit():
                raise RuntimeError("synthetic failure - disk full simulado")

            monkeypatch.setattr(db.session, "commit", _raise_commit)

            with pytest.raises(ProductAccessOperationError) as exc_info:
                AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)

            # Tipo de erro correto (não ProductAccessError - isto não é
            # validação conhecida, é falha inesperada de infraestrutura).
            assert not isinstance(exc_info.value, ProductAccessError)
            # Causa técnica original preservada via `raise ... from exc`,
            # mas nunca exposta na mensagem pública da exceção.
            assert isinstance(exc_info.value.__cause__, RuntimeError)
            assert "disk full simulado" not in str(exc_info.value)

            assert OrganizationProduct.query.count() == 0
            assert AuditLog.query.filter_by(action="organization.product.granted").count() == 0


class TestImmediateReflection:
    def test_end_user_launcher_reflects_grant_and_revoke_immediately(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org = _create_organization()
            actor = _create_actor()

            member = User(
                name="Membro Issue 30",
                email="membro.issue30@example.com",
                email_verified_at=datetime.utcnow(),
            )
            member.set_password(SYNTHETIC_PASSWORD)
            db.session.add(member)
            db.session.commit()
            OrganizationService.add_member(org.id, member.id, "member")

            AccessService.grant_product_access(org.id, "gedo", actor_user_id=actor.id)
            items = AccessService.get_organization_products(member.id, org.id)
            by_code = {item["product"].code: item for item in items}
            assert by_code["gedo"]["has_access"] is True

            AccessService.revoke_product_access(org.id, "gedo", actor_user_id=actor.id)
            items = AccessService.get_organization_products(member.id, org.id)
            by_code = {item["product"].code: item for item in items}
            assert by_code["gedo"]["has_access"] is False
