"""Issue #64: credencial simétrica por OrganizationProductInstallation -
fundação para a autenticação servidor-servidor da federação (Issue #62).
Substitui a ideia de segredo global (`GEDO_LAUNCH_SECRET`) por credencial
própria de cada instalação. Cobre exclusivamente model, migration e
service (`InstallationCredentialService`) - nenhuma rota/UI existe ainda,
por isso não há teste HTTP nem teste manual nesta Issue."""
import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    AuditLog,
    Organization,
    OrganizationProduct,
    OrganizationProductInstallation,
    OrganizationProductInstallationCredential,
    Product,
    User,
)
from app.services.bootstrap_service import BootstrapService
from app.services.installation_credential_service import (
    InstallationCredentialError,
    InstallationCredentialOperationError,
    InstallationCredentialService,
)
from app.services.organization_service import OrganizationService
import app.services.installation_credential_service as credential_service_module

SYNTHETIC_PASSWORD = "senha-sintetica-issue-64-correcao-123"


def _create_organization(legal_name=None):
    org = Organization(legal_name=legal_name or f"Organizacao Issue 64 {uuid.uuid4().hex[:8]}")
    db.session.add(org)
    db.session.commit()
    return org


def _create_product(code=None, name="Produto Issue 64", url="https://produto-issue-64.local"):
    product = Product(code=code or f"produto-issue64-{uuid.uuid4().hex[:8]}", name=name,
                       description=f"Descricao {name}", url=url)
    db.session.add(product)
    db.session.commit()
    return product


def _create_org_product(org, product, status="active"):
    org_product = OrganizationProduct(organization_id=org.id, product_id=product.id, status=status)
    db.session.add(org_product)
    db.session.commit()
    return org_product


def _create_installation(org_product, url="https://instalacao-issue-64.local", is_active=True):
    installation = OrganizationProductInstallation(
        organization_product_id=org_product.id, url=url, is_active=is_active,
    )
    db.session.add(installation)
    db.session.commit()
    return installation


def _new_installation(is_active=True, org_product_status="active"):
    """Monta organização + produto + org_product + instalação - sem
    nenhuma credencial ainda."""
    org = _create_organization()
    product = _create_product()
    org_product = _create_org_product(org, product, status=org_product_status)
    return _create_installation(org_product, is_active=is_active)


def _installation_with_credential(is_active=True):
    """Conveniência para os testes que só precisam do estado final: uma
    instalação já com sua primeira credencial emitida."""
    installation = _new_installation(is_active=is_active)
    secret = InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)
    return installation, secret


def _create_user(email, *, name="Usuario Issue 64"):
    user = User(name=name, email=email, email_verified_at=datetime.utcnow())
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


class TestCredentialModelAndMigration:
    def test_creates_credential_without_plaintext_secret_column(self, app):
        with app.app_context():
            installation = _new_installation()
            secret = InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)

            credential = OrganizationProductInstallationCredential.query.filter_by(
                organization_product_installation_id=installation.id
            ).first()
            assert credential is not None
            # O model não tem nenhuma coluna de segredo em claro - só
            # `secret_hash`, e ele nunca é igual ao segredo original.
            assert not hasattr(credential, 'secret')
            assert not hasattr(credential, 'plaintext_secret')
            assert credential.secret_hash != secret
            assert len(credential.secret_hash) == 64  # SHA-256 hexdigest, sem truncamento
            assert credential.secret_hash == hashlib.sha256(secret.encode('utf-8')).hexdigest()

    def test_fk_and_relationship_both_directions(self, app):
        with app.app_context():
            installation = _new_installation()
            InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)

            db.session.expire_all()

            reloaded_installation = OrganizationProductInstallation.query.get(installation.id)
            assert len(reloaded_installation.credentials) == 1

            credential = reloaded_installation.credentials[0]
            assert credential.organization_product_installation is not None
            assert credential.organization_product_installation.id == installation.id

    def test_physical_deletion_of_installation_cascades_to_credentials(self, app):
        with app.app_context():
            installation = _new_installation()
            InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)
            assert OrganizationProductInstallationCredential.query.count() == 1

            # Exclusão física real da instalação (nunca exercitada pelo
            # fluxo normal) - deve remover a credencial em cascata.
            db.session.delete(OrganizationProductInstallation.query.get(installation.id))
            db.session.commit()

            assert OrganizationProductInstallationCredential.query.count() == 0

    def test_partial_unique_index_rejects_second_active_credential(self, app):
        with app.app_context():
            installation = _new_installation()
            first = OrganizationProductInstallationCredential(
                organization_product_installation_id=installation.id,
                secret_hash=hashlib.sha256(b"primeiro").hexdigest(),
            )
            db.session.add(first)
            db.session.commit()

            second = OrganizationProductInstallationCredential(
                organization_product_installation_id=installation.id,
                secret_hash=hashlib.sha256(b"segundo").hexdigest(),
            )
            db.session.add(second)
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()

            assert OrganizationProductInstallationCredential.query.filter_by(
                organization_product_installation_id=installation.id, revoked_at=None,
            ).count() == 1

    def test_migration_chain_has_single_head(self):
        # Verificação estática leve, sem tocar nenhum banco: confirma que
        # nenhuma outra migration aponta para a mesma down_revision que a
        # nova migration desta Issue - encadeamento linear, sem heads
        # paralelos.
        import re
        from pathlib import Path

        migrations_dir = Path(__file__).resolve().parents[1] / "migrations" / "versions"
        down_revisions = []
        for path in migrations_dir.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            match = re.search(r"^down_revision = (.+)$", text, re.MULTILINE)
            assert match is not None, f"{path.name} sem down_revision"
            down_revisions.append(match.group(1).strip())

        non_none = [d for d in down_revisions if d != "None"]
        assert len(non_none) == len(set(non_none)), "mais de uma migration aponta para a mesma down_revision"


class TestIssueCredential:
    def test_returns_high_entropy_urlsafe_secret(self, app):
        with app.app_context():
            installation = _new_installation()
            secret = InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)

            # token_urlsafe(32) produz uma string de tamanho fixo,
            # composta só por caracteres seguros para header HTTP.
            assert isinstance(secret, str)
            assert len(secret) >= 40
            allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
            assert set(secret) <= allowed

    def test_secret_never_persisted_in_clear(self, app):
        with app.app_context():
            installation = _new_installation()
            secret = InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)

            all_hashes = [c.secret_hash for c in OrganizationProductInstallationCredential.query.all()]
            assert secret not in all_hashes
            # Reconsultado do zero (nunca confiar só no objeto em memória).
            db.session.expire_all()
            credential = OrganizationProductInstallationCredential.query.filter_by(
                organization_product_installation_id=installation.id,
            ).first()
            assert credential.secret_hash != secret

    def test_repeated_issuance_rejected_without_second_credential(self, app):
        with app.app_context():
            installation, _ = _installation_with_credential()

            with pytest.raises(InstallationCredentialError):
                InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)

            assert OrganizationProductInstallationCredential.query.filter_by(
                organization_product_installation_id=installation.id
            ).count() == 1

    def test_issuance_for_unknown_installation_rejected(self, app):
        with app.app_context():
            with pytest.raises(InstallationCredentialError):
                InstallationCredentialService.issue_credential(uuid.uuid4(), actor_user_id=None)

    def test_exactly_one_audit_event_on_issuance(self, app):
        with app.app_context():
            installation = _new_installation()
            before = AuditLog.query.filter_by(action='organization_product_installation.credential_issued').count()

            InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)

            assert AuditLog.query.filter_by(
                action='organization_product_installation.credential_issued'
            ).count() == before + 1

    def test_lock_is_acquired_during_issuance(self, app, monkeypatch):
        with app.app_context():
            installation = _new_installation()
            calls = []
            original = InstallationCredentialService._lock_installation_row

            def _spy(installation_id):
                calls.append(installation_id)
                return original(installation_id)

            monkeypatch.setattr(InstallationCredentialService, "_lock_installation_row", staticmethod(_spy))

            InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)

            assert calls == [installation.id]

    def test_rollback_on_unexpected_failure_leaves_no_credential_or_audit(self, app, monkeypatch):
        with app.app_context():
            installation = _new_installation()
            audit_before = AuditLog.query.count()

            def _raise():
                raise RuntimeError("falha sintetica de flush")
            monkeypatch.setattr(db.session, "flush", _raise)

            with pytest.raises(InstallationCredentialOperationError) as exc_info:
                InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)

            assert isinstance(exc_info.value.__cause__, RuntimeError)

        with app.app_context():
            assert OrganizationProductInstallationCredential.query.count() == 0
            assert AuditLog.query.count() == audit_before

    def test_integrity_error_converted_to_operation_error_never_leaks_raw(self, app, monkeypatch):
        with app.app_context():
            installation = _new_installation()

            def _raise():
                raise IntegrityError("synthetic", {}, Exception("synthetic"))
            monkeypatch.setattr(db.session, "flush", _raise)

            with pytest.raises(InstallationCredentialOperationError) as exc_info:
                InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)

            assert isinstance(exc_info.value.__cause__, IntegrityError)
            # A mensagem pública nunca é a exceção crua do driver/banco.
            assert "IntegrityError" not in str(exc_info.value)
            assert "synthetic" not in str(exc_info.value)


class TestAuthenticateInstallation:
    def test_correct_secret_authenticates(self, app):
        with app.app_context():
            installation, secret = _installation_with_credential()

            result = InstallationCredentialService.authenticate_installation(installation.public_id, secret)

            assert result is not None
            assert result.id == installation.id

    def test_wrong_secret_rejected(self, app):
        with app.app_context():
            installation, _ = _installation_with_credential()

            result = InstallationCredentialService.authenticate_installation(
                installation.public_id, "segredo-completamente-errado"
            )

            assert result is None

    def test_unknown_public_id_rejected(self, app):
        with app.app_context():
            result = InstallationCredentialService.authenticate_installation(uuid.uuid4(), "qualquer-coisa")
            assert result is None

    def test_installation_without_credential_rejected(self, app):
        with app.app_context():
            installation = _new_installation()
            result = InstallationCredentialService.authenticate_installation(
                installation.public_id, "qualquer-coisa"
            )
            assert result is None

    def test_credential_from_another_installation_rejected(self, app):
        with app.app_context():
            installation_a, secret_a = _installation_with_credential()
            installation_b, _secret_b = _installation_with_credential()

            # Apresenta o public_id de B com o segredo de A.
            result = InstallationCredentialService.authenticate_installation(installation_b.public_id, secret_a)
            assert result is None

    def test_revoked_credential_rejected(self, app):
        with app.app_context():
            installation, secret = _installation_with_credential()
            InstallationCredentialService.revoke_credential(installation.id, actor_user_id=None)

            result = InstallationCredentialService.authenticate_installation(installation.public_id, secret)
            assert result is None

    def test_inactive_installation_rejected_even_with_correct_secret(self, app):
        with app.app_context():
            installation, secret = _installation_with_credential()
            installation.is_active = False
            db.session.commit()

            result = InstallationCredentialService.authenticate_installation(installation.public_id, secret)
            assert result is None

    def test_empty_presented_secret_rejected(self, app):
        with app.app_context():
            installation, _secret = _installation_with_credential()
            result = InstallationCredentialService.authenticate_installation(installation.public_id, "")
            assert result is None

    def test_uses_constant_time_comparison(self, app, monkeypatch):
        with app.app_context():
            installation, secret = _installation_with_credential()
            calls = []
            original = hmac.compare_digest

            def _spy(a, b):
                calls.append((a, b))
                return original(a, b)

            monkeypatch.setattr(credential_service_module.hmac, "compare_digest", _spy)

            InstallationCredentialService.authenticate_installation(installation.public_id, secret)

            assert len(calls) == 1

    def test_dummy_comparison_executed_for_unknown_public_id(self, app, monkeypatch):
        with app.app_context():
            calls = []
            original = hmac.compare_digest

            def _spy(a, b):
                calls.append((a, b))
                return original(a, b)

            monkeypatch.setattr(credential_service_module.hmac, "compare_digest", _spy)

            InstallationCredentialService.authenticate_installation(uuid.uuid4(), "qualquer-coisa")

            # Mesmo sem nenhum candidato real, a comparação ainda ocorre
            # (contra o hash dummy fixo) - nunca um "return None" sem
            # nenhuma operação de hash/comparação correspondente.
            assert len(calls) == 1
            assert calls[0][1] == credential_service_module._DUMMY_SECRET_HASH

    def test_authentication_never_mutates_or_creates_audit_log(self, app):
        with app.app_context():
            installation, secret = _installation_with_credential()

            with app.app_context():
                audit_before = AuditLog.query.count()
                credentials_before = OrganizationProductInstallationCredential.query.count()

            InstallationCredentialService.authenticate_installation(installation.public_id, secret)
            InstallationCredentialService.authenticate_installation(installation.public_id, "errado")

            assert AuditLog.query.count() == audit_before
            assert OrganizationProductInstallationCredential.query.count() == credentials_before


class TestRotateCredential:
    def test_new_credential_authenticates(self, app):
        with app.app_context():
            installation, _old_secret = _installation_with_credential()
            new_secret = InstallationCredentialService.rotate_credential(installation.id, actor_user_id=None)

            result = InstallationCredentialService.authenticate_installation(installation.public_id, new_secret)
            assert result is not None
            assert result.id == installation.id

    def test_previous_credential_still_works_during_grace_window(self, app):
        with app.app_context():
            installation, old_secret = _installation_with_credential()
            InstallationCredentialService.rotate_credential(installation.id, actor_user_id=None)

            result = InstallationCredentialService.authenticate_installation(installation.public_id, old_secret)
            assert result is not None

    def test_previous_credential_fails_after_grace_window_expires(self, app):
        with app.app_context():
            installation, old_secret = _installation_with_credential()
            InstallationCredentialService.rotate_credential(installation.id, actor_user_id=None)

            # Simula a expiração da janela: move `revoked_at` da
            # credencial anterior para o passado (leitura/escrita direta
            # somente para simular o relógio avançar - nunca usada para
            # contornar o service em produção).
            previous = OrganizationProductInstallationCredential.query.filter(
                OrganizationProductInstallationCredential.organization_product_installation_id == installation.id,
                OrganizationProductInstallationCredential.revoked_at.isnot(None),
            ).first()
            previous.revoked_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.session.commit()

            result = InstallationCredentialService.authenticate_installation(installation.public_id, old_secret)
            assert result is None

    def test_new_credential_unaffected_by_previous_window_expiring(self, app):
        with app.app_context():
            installation, _old_secret = _installation_with_credential()
            new_secret = InstallationCredentialService.rotate_credential(installation.id, actor_user_id=None)

            previous = OrganizationProductInstallationCredential.query.filter(
                OrganizationProductInstallationCredential.organization_product_installation_id == installation.id,
                OrganizationProductInstallationCredential.revoked_at.isnot(None),
            ).first()
            previous.revoked_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.session.commit()

            result = InstallationCredentialService.authenticate_installation(installation.public_id, new_secret)
            assert result is not None

    def test_exactly_one_audit_event_on_rotation(self, app):
        with app.app_context():
            installation, _secret = _installation_with_credential()
            before = AuditLog.query.filter_by(action='organization_product_installation.credential_rotated').count()

            InstallationCredentialService.rotate_credential(installation.id, actor_user_id=None)

            assert AuditLog.query.filter_by(
                action='organization_product_installation.credential_rotated'
            ).count() == before + 1

    def test_rotation_without_active_credential_rejected(self, app):
        with app.app_context():
            installation = _new_installation()
            with pytest.raises(InstallationCredentialError):
                InstallationCredentialService.rotate_credential(installation.id, actor_user_id=None)

    def test_repeated_rotation_never_leaves_more_than_two_accepted_credentials(self, app):
        with app.app_context():
            installation, _secret = _installation_with_credential()

            InstallationCredentialService.rotate_credential(installation.id, actor_user_id=None)
            InstallationCredentialService.rotate_credential(installation.id, actor_user_id=None)
            InstallationCredentialService.rotate_credential(installation.id, actor_user_id=None)

            now = datetime.now(timezone.utc)
            accepted = OrganizationProductInstallationCredential.query.filter(
                OrganizationProductInstallationCredential.organization_product_installation_id == installation.id,
                db.or_(
                    OrganizationProductInstallationCredential.revoked_at.is_(None),
                    OrganizationProductInstallationCredential.revoked_at > now,
                ),
            ).count()
            assert accepted <= 2

    def test_at_most_one_row_with_null_revoked_at_after_multiple_rotations(self, app):
        with app.app_context():
            installation, _secret = _installation_with_credential()
            InstallationCredentialService.rotate_credential(installation.id, actor_user_id=None)
            InstallationCredentialService.rotate_credential(installation.id, actor_user_id=None)

            current_count = OrganizationProductInstallationCredential.query.filter_by(
                organization_product_installation_id=installation.id, revoked_at=None,
            ).count()
            assert current_count == 1

    def test_historical_credential_rows_preserved_never_deleted(self, app):
        with app.app_context():
            installation, _secret = _installation_with_credential()
            InstallationCredentialService.rotate_credential(installation.id, actor_user_id=None)
            InstallationCredentialService.rotate_credential(installation.id, actor_user_id=None)

            # 1 emissão inicial + 2 rotações = 3 linhas no total, nenhuma
            # jamais excluída.
            assert OrganizationProductInstallationCredential.query.filter_by(
                organization_product_installation_id=installation.id,
            ).count() == 3

    def test_rollback_on_rotation_failure_leaves_state_unchanged(self, app, monkeypatch):
        with app.app_context():
            installation, secret = _installation_with_credential()
            installation_id = installation.id
            installation_public_id = installation.public_id
            audit_before = AuditLog.query.count()
            credentials_before = OrganizationProductInstallationCredential.query.count()

            def _raise():
                raise RuntimeError("falha sintetica de commit")
            monkeypatch.setattr(db.session, "commit", _raise)

            with pytest.raises(InstallationCredentialOperationError):
                InstallationCredentialService.rotate_credential(installation_id, actor_user_id=None)

        with app.app_context():
            assert AuditLog.query.count() == audit_before
            assert OrganizationProductInstallationCredential.query.count() == credentials_before
            # A credencial original continua válida - a rotação não
            # deixou nenhum estado parcial.
            result = InstallationCredentialService.authenticate_installation(installation_public_id, secret)
            assert result is not None


class TestRevokeCredential:
    def test_all_accepted_credentials_revoked_immediately(self, app):
        with app.app_context():
            installation, old_secret = _installation_with_credential()
            new_secret = InstallationCredentialService.rotate_credential(installation.id, actor_user_id=None)

            InstallationCredentialService.revoke_credential(installation.id, actor_user_id=None)

            assert InstallationCredentialService.authenticate_installation(
                installation.public_id, old_secret
            ) is None
            assert InstallationCredentialService.authenticate_installation(
                installation.public_id, new_secret
            ) is None

    def test_authentication_fails_after_revocation(self, app):
        with app.app_context():
            installation, secret = _installation_with_credential()
            InstallationCredentialService.revoke_credential(installation.id, actor_user_id=None)

            assert InstallationCredentialService.authenticate_installation(installation.public_id, secret) is None

    def test_repeated_revocation_rejected_without_second_success_event(self, app):
        with app.app_context():
            installation, _secret = _installation_with_credential()
            InstallationCredentialService.revoke_credential(installation.id, actor_user_id=None)

            with pytest.raises(InstallationCredentialError):
                InstallationCredentialService.revoke_credential(installation.id, actor_user_id=None)

            assert AuditLog.query.filter_by(
                action='organization_product_installation.credential_revoked'
            ).count() == 1

    def test_revocation_without_accepted_credential_rejected(self, app):
        with app.app_context():
            installation = _new_installation()
            with pytest.raises(InstallationCredentialError):
                InstallationCredentialService.revoke_credential(installation.id, actor_user_id=None)

    def test_exactly_one_audit_event_per_real_revocation(self, app):
        with app.app_context():
            installation, _secret = _installation_with_credential()
            before = AuditLog.query.filter_by(action='organization_product_installation.credential_revoked').count()

            InstallationCredentialService.revoke_credential(installation.id, actor_user_id=None)

            assert AuditLog.query.filter_by(
                action='organization_product_installation.credential_revoked'
            ).count() == before + 1

    def test_revocation_never_physically_deletes_rows(self, app):
        with app.app_context():
            installation, _secret = _installation_with_credential()
            total_before = OrganizationProductInstallationCredential.query.count()

            InstallationCredentialService.revoke_credential(installation.id, actor_user_id=None)

            assert OrganizationProductInstallationCredential.query.count() == total_before


class TestPreservation:
    def test_installation_fields_unchanged_through_full_cycle(self, app):
        with app.app_context():
            installation, secret = _installation_with_credential()
            original_public_id = installation.public_id
            original_url = installation.url
            original_id = installation.id

            InstallationCredentialService.rotate_credential(installation.id, actor_user_id=None)
            InstallationCredentialService.revoke_credential(installation.id, actor_user_id=None)

            reloaded = OrganizationProductInstallation.query.get(original_id)
            assert reloaded.public_id == original_public_id
            assert reloaded.url == original_url
            assert reloaded.is_active is True

    def test_organization_product_status_unaffected(self, app):
        with app.app_context():
            org = _create_organization()
            product = _create_product()
            org_product = _create_org_product(org, product, status="active")
            installation = _create_installation(org_product)

            InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)
            InstallationCredentialService.rotate_credential(installation.id, actor_user_id=None)
            InstallationCredentialService.revoke_credential(installation.id, actor_user_id=None)

            reloaded = OrganizationProduct.query.get(org_product.id)
            assert reloaded.status == "active"

    def test_bootstrap_does_not_create_credentials(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            assert OrganizationProductInstallationCredential.query.count() == 0

    def test_no_secret_leaks_in_domain_error_message(self, app):
        with app.app_context():
            installation, secret = _installation_with_credential()

            try:
                InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)
                assert False, "esperado InstallationCredentialError"
            except InstallationCredentialError as exc:
                assert secret not in str(exc)

    def test_no_secret_or_hash_in_audit_log_details(self, app):
        with app.app_context():
            installation, secret = _installation_with_credential()
            new_secret = InstallationCredentialService.rotate_credential(installation.id, actor_user_id=None)
            InstallationCredentialService.revoke_credential(installation.id, actor_user_id=None)

            secret_hash = hashlib.sha256(secret.encode('utf-8')).hexdigest()
            new_secret_hash = hashlib.sha256(new_secret.encode('utf-8')).hexdigest()

            logs = AuditLog.query.filter(
                AuditLog.action.like('organization_product_installation.credential_%')
            ).all()
            assert len(logs) >= 3
            for log in logs:
                details_str = str(log.details)
                assert secret not in details_str
                assert new_secret not in details_str
                assert secret_hash not in details_str
                assert new_secret_hash not in details_str


# ---------------------------------------------------------------------------
# Correção dos Achados A2 e M1 da revisão técnica da Issue #64: regressões
# adicionais para a validação/normalização de `public_id`/`presented_secret`
# em `authenticate_installation` (A2) e de `installation_id` nas operações
# administrativas (M1), além da eliminação da ambiguidade B1 sobre
# instalação inativa.
# ---------------------------------------------------------------------------

# Entradas inválidas de `public_id` que NUNCA podem propagar exceção e
# devem sempre resultar em `None` (Achado A2). "not-a-uuid" cobre string
# não-UUID; os demais cobrem tipos completamente alheios ao formato
# esperado.
INVALID_PUBLIC_IDS = [
    None,
    "",
    "not-a-uuid",
    12345,
    [],
    {},
    {"public_id": "x"},
]

# Entradas inválidas de `presented_secret` que também nunca podem propagar
# exceção nem autenticar (Achado A2) - só `str` não vazia é candidato real.
INVALID_SECRETS = [
    None,
    "",
    12345,
    [],
    {},
    b"segredo-em-bytes",
]

# Entradas inválidas de `installation_id` para as operações administrativas
# (Achado M1) - devem sempre virar `InstallationCredentialError`, nunca
# `InstallationCredentialOperationError`.
INVALID_INSTALLATION_IDS = [
    None,
    "",
    "not-a-uuid",
    12345,
    [],
    {},
]


class TestAuthenticateInstallationInputValidation:
    """Achado A2: `authenticate_installation` nunca deve propagar exceção
    nem conceder acesso para entrada inválida/malformada de `public_id`
    ou `presented_secret`."""

    def test_valid_uuid_object_public_id_authenticates(self, app):
        with app.app_context():
            installation, secret = _installation_with_credential()
            assert isinstance(installation.public_id, uuid.UUID)

            result = InstallationCredentialService.authenticate_installation(installation.public_id, secret)
            assert result is not None
            assert result.id == installation.id

    def test_valid_uuid_string_public_id_authenticates(self, app):
        with app.app_context():
            installation, secret = _installation_with_credential()

            result = InstallationCredentialService.authenticate_installation(str(installation.public_id), secret)
            assert result is not None
            assert result.id == installation.id

    @pytest.mark.parametrize("invalid_public_id", INVALID_PUBLIC_IDS)
    def test_invalid_public_id_returns_none_without_raising(self, app, invalid_public_id):
        with app.app_context():
            _installation, secret = _installation_with_credential()

            result = InstallationCredentialService.authenticate_installation(invalid_public_id, secret)
            assert result is None

    @pytest.mark.parametrize("invalid_secret", INVALID_SECRETS)
    def test_invalid_secret_returns_none_without_raising(self, app, invalid_secret):
        with app.app_context():
            installation, _secret = _installation_with_credential()

            result = InstallationCredentialService.authenticate_installation(installation.public_id, invalid_secret)
            assert result is None

    def test_secret_with_added_whitespace_never_authenticates(self, app):
        with app.app_context():
            installation, secret = _installation_with_credential()

            # `.strip()` NUNCA deve ser aplicado ao segredo apresentado -
            # espaço em branco adicionado faz parte do valor apresentado e
            # deve simplesmente falhar, nunca ser silenciosamente
            # normalizado para o segredo correto.
            assert InstallationCredentialService.authenticate_installation(
                installation.public_id, f" {secret}"
            ) is None
            assert InstallationCredentialService.authenticate_installation(
                installation.public_id, f"{secret} "
            ) is None
            assert InstallationCredentialService.authenticate_installation(
                installation.public_id, f" {secret} "
            ) is None

    def test_invalid_public_id_and_invalid_secret_together_returns_none(self, app):
        with app.app_context():
            _installation, _secret = _installation_with_credential()

            for invalid_public_id in INVALID_PUBLIC_IDS:
                for invalid_secret in INVALID_SECRETS:
                    result = InstallationCredentialService.authenticate_installation(
                        invalid_public_id, invalid_secret
                    )
                    assert result is None

    @pytest.mark.parametrize("invalid_public_id", INVALID_PUBLIC_IDS)
    def test_dummy_comparison_executed_for_invalid_public_id(self, app, monkeypatch, invalid_public_id):
        with app.app_context():
            calls = []
            original = hmac.compare_digest

            def _spy(a, b):
                calls.append((a, b))
                return original(a, b)

            monkeypatch.setattr(credential_service_module.hmac, "compare_digest", _spy)

            InstallationCredentialService.authenticate_installation(invalid_public_id, "qualquer-coisa-valida")

            assert len(calls) == 1

    @pytest.mark.parametrize("invalid_secret", INVALID_SECRETS)
    def test_dummy_comparison_executed_for_invalid_secret(self, app, monkeypatch, invalid_secret):
        with app.app_context():
            installation, _secret = _installation_with_credential()
            calls = []
            original = hmac.compare_digest

            def _spy(a, b):
                calls.append((a, b))
                return original(a, b)

            monkeypatch.setattr(credential_service_module.hmac, "compare_digest", _spy)

            InstallationCredentialService.authenticate_installation(installation.public_id, invalid_secret)

            assert len(calls) == 1

    def test_no_mutation_or_audit_for_invalid_input(self, app):
        with app.app_context():
            installation, secret = _installation_with_credential()
            audit_before = AuditLog.query.count()
            credentials_before = OrganizationProductInstallationCredential.query.count()

            for invalid_public_id in INVALID_PUBLIC_IDS:
                InstallationCredentialService.authenticate_installation(invalid_public_id, secret)
            for invalid_secret in INVALID_SECRETS:
                InstallationCredentialService.authenticate_installation(installation.public_id, invalid_secret)

            assert AuditLog.query.count() == audit_before
            assert OrganizationProductInstallationCredential.query.count() == credentials_before

    def test_correct_credential_still_authenticates_after_fix(self, app):
        with app.app_context():
            installation, secret = _installation_with_credential()
            result = InstallationCredentialService.authenticate_installation(installation.public_id, secret)
            assert result is not None
            assert result.id == installation.id

    def test_previous_credential_during_rotation_window_still_authenticates_after_fix(self, app):
        with app.app_context():
            installation, old_secret = _installation_with_credential()
            InstallationCredentialService.rotate_credential(installation.id, actor_user_id=None)

            result = InstallationCredentialService.authenticate_installation(installation.public_id, old_secret)
            assert result is not None
            assert result.id == installation.id


class TestAdministrativeOperationsIdNormalization:
    """Achado M1: `issue_credential`, `rotate_credential` e
    `revoke_credential` devem normalizar `installation_id` explicitamente,
    antes de qualquer lock/consulta/mutação, e um valor inválido deve
    sempre virar `InstallationCredentialError` (erro de domínio), nunca
    `InstallationCredentialOperationError`."""

    def test_issue_credential_accepts_string_uuid_installation_id(self, app):
        with app.app_context():
            installation = _new_installation()
            secret = InstallationCredentialService.issue_credential(str(installation.id), actor_user_id=None)

            result = InstallationCredentialService.authenticate_installation(installation.public_id, secret)
            assert result is not None

    def test_rotate_credential_accepts_string_uuid_installation_id(self, app):
        with app.app_context():
            installation, _old_secret = _installation_with_credential()
            new_secret = InstallationCredentialService.rotate_credential(str(installation.id), actor_user_id=None)

            result = InstallationCredentialService.authenticate_installation(installation.public_id, new_secret)
            assert result is not None

    def test_revoke_credential_accepts_string_uuid_installation_id(self, app):
        with app.app_context():
            installation, secret = _installation_with_credential()
            InstallationCredentialService.revoke_credential(str(installation.id), actor_user_id=None)

            result = InstallationCredentialService.authenticate_installation(installation.public_id, secret)
            assert result is None

    @pytest.mark.parametrize("invalid_id", INVALID_INSTALLATION_IDS)
    def test_issue_credential_invalid_id_raises_domain_error(self, app, invalid_id):
        with app.app_context():
            with pytest.raises(InstallationCredentialError) as exc_info:
                InstallationCredentialService.issue_credential(invalid_id, actor_user_id=None)
            assert not isinstance(exc_info.value, InstallationCredentialOperationError)

    @pytest.mark.parametrize("invalid_id", INVALID_INSTALLATION_IDS)
    def test_rotate_credential_invalid_id_raises_domain_error(self, app, invalid_id):
        with app.app_context():
            with pytest.raises(InstallationCredentialError) as exc_info:
                InstallationCredentialService.rotate_credential(invalid_id, actor_user_id=None)
            assert not isinstance(exc_info.value, InstallationCredentialOperationError)

    @pytest.mark.parametrize("invalid_id", INVALID_INSTALLATION_IDS)
    def test_revoke_credential_invalid_id_raises_domain_error(self, app, invalid_id):
        with app.app_context():
            with pytest.raises(InstallationCredentialError) as exc_info:
                InstallationCredentialService.revoke_credential(invalid_id, actor_user_id=None)
            assert not isinstance(exc_info.value, InstallationCredentialOperationError)

    def test_invalid_id_never_touches_database_or_audit(self, app):
        with app.app_context():
            audit_before = AuditLog.query.count()
            credentials_before = OrganizationProductInstallationCredential.query.count()

            for invalid_id in INVALID_INSTALLATION_IDS:
                for operation in (
                    InstallationCredentialService.issue_credential,
                    InstallationCredentialService.rotate_credential,
                    InstallationCredentialService.revoke_credential,
                ):
                    with pytest.raises(InstallationCredentialError):
                        operation(invalid_id, actor_user_id=None)

            assert AuditLog.query.count() == audit_before
            assert OrganizationProductInstallationCredential.query.count() == credentials_before

    def test_invalid_id_message_never_includes_raw_value(self, app):
        with app.app_context():
            suspicious_value = "<<valor-bruto-suspeito-nao-deve-vazar>>"
            with pytest.raises(InstallationCredentialError) as exc_info:
                InstallationCredentialService.issue_credential(suspicious_value, actor_user_id=None)
            assert suspicious_value not in str(exc_info.value)


class TestFullIssueRevokeIssueCycle:
    def test_issue_revoke_issue_again_succeeds(self, app):
        with app.app_context():
            installation, first_secret = _installation_with_credential()

            InstallationCredentialService.revoke_credential(installation.id, actor_user_id=None)
            second_secret = InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)

            assert InstallationCredentialService.authenticate_installation(
                installation.public_id, second_secret
            ) is not None
            assert InstallationCredentialService.authenticate_installation(
                installation.public_id, first_secret
            ) is None

    def test_full_cycle_preserves_history(self, app):
        with app.app_context():
            installation, _first_secret = _installation_with_credential()
            InstallationCredentialService.revoke_credential(installation.id, actor_user_id=None)
            InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)

            # 1a emissão + 2a emissão = 2 linhas, nenhuma excluída.
            assert OrganizationProductInstallationCredential.query.filter_by(
                organization_product_installation_id=installation.id,
            ).count() == 2

    def test_full_cycle_generates_exactly_expected_audit_events(self, app):
        with app.app_context():
            installation = _new_installation()
            InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)
            InstallationCredentialService.revoke_credential(installation.id, actor_user_id=None)
            InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)

            issued = AuditLog.query.filter_by(
                action='organization_product_installation.credential_issued'
            ).count()
            revoked = AuditLog.query.filter_by(
                action='organization_product_installation.credential_revoked'
            ).count()
            assert issued == 2
            assert revoked == 1


class TestInactiveInstallationAdministrativeOperations:
    """Regra documentada (Seção 4 / eliminação da ambiguidade B1):
    operações administrativas (emitir/rotacionar/revogar) PODEM operar
    sobre uma instalação inativa (pré-provisionamento, manutenção,
    revogação de segurança); autenticação usando credencial de instalação
    inativa SEMPRE retorna `None`; ativar não exige gerar nova credencial;
    desativar não revoga/rotaciona a credencial existente."""

    def test_issue_credential_succeeds_on_inactive_installation(self, app):
        with app.app_context():
            installation = _new_installation(is_active=False)
            secret = InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)
            assert secret is not None
            assert OrganizationProductInstallationCredential.query.filter_by(
                organization_product_installation_id=installation.id
            ).count() == 1

    def test_rotate_credential_succeeds_on_inactive_installation(self, app):
        with app.app_context():
            installation, _old_secret = _installation_with_credential(is_active=False)
            new_secret = InstallationCredentialService.rotate_credential(installation.id, actor_user_id=None)
            assert new_secret is not None

    def test_revoke_credential_succeeds_on_inactive_installation(self, app):
        with app.app_context():
            installation, _secret = _installation_with_credential(is_active=False)
            InstallationCredentialService.revoke_credential(installation.id, actor_user_id=None)
            assert OrganizationProductInstallationCredential.query.filter_by(
                organization_product_installation_id=installation.id, revoked_at=None,
            ).count() == 0

    def test_no_credential_authenticates_while_installation_inactive(self, app):
        with app.app_context():
            installation, secret = _installation_with_credential(is_active=False)
            result = InstallationCredentialService.authenticate_installation(installation.public_id, secret)
            assert result is None

    def test_reactivation_authenticates_without_recreating_credential(self, app):
        with app.app_context():
            installation, secret = _installation_with_credential(is_active=False)

            original_credential = OrganizationProductInstallationCredential.query.filter_by(
                organization_product_installation_id=installation.id
            ).first()
            original_credential_id = original_credential.id

            # Ativação é uma mudança direta de `is_active` - nenhuma rota/
            # evento/coluna nova foi introduzida por esta correção; o
            # service de credencial não participa da ativação/desativação.
            installation.is_active = True
            db.session.commit()

            result = InstallationCredentialService.authenticate_installation(installation.public_id, secret)
            assert result is not None
            assert result.id == installation.id

            still_only_credential = OrganizationProductInstallationCredential.query.filter_by(
                organization_product_installation_id=installation.id
            ).all()
            assert len(still_only_credential) == 1
            assert still_only_credential[0].id == original_credential_id

    def test_deactivation_does_not_revoke_or_rotate_credential(self, app):
        with app.app_context():
            installation, secret = _installation_with_credential(is_active=True)
            original_credential = OrganizationProductInstallationCredential.query.filter_by(
                organization_product_installation_id=installation.id
            ).first()

            installation.is_active = False
            db.session.commit()

            reloaded = OrganizationProductInstallationCredential.query.filter_by(
                organization_product_installation_id=installation.id
            ).first()
            assert reloaded.id == original_credential.id
            assert reloaded.revoked_at is None
            assert reloaded.secret_hash == original_credential.secret_hash


class TestLauncherCompatibilityWithCredentials:
    def test_launcher_rendering_unaffected_by_presence_of_credential(self, client, app, get_csrf_token):
        with app.app_context():
            org = _create_organization("Organizacao Issue 64 Launcher Correcao")
            user = _create_user("membro.issue64.correcao@example.com")
            product = _create_product(
                code="produto-issue64-launcher-correcao",
                url="https://produto-issue64-launcher-correcao.local",
            )
            OrganizationService.add_member(org.id, user.id, "member")
            org_product = _create_org_product(org, product, status="active")
            installation = _create_installation(org_product)
            InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)
            InstallationCredentialService.rotate_credential(installation.id, actor_user_id=None)

        _login(client, get_csrf_token, "membro.issue64.correcao@example.com")
        response = client.get("/")
        assert response.status_code == 200
        html = response.data.decode("utf-8")

        assert 'href="https://produto-issue64-launcher-correcao.local"' in html
        assert "Acessar Sistema" in html

    def test_launcher_never_renders_secret_hash_or_public_id(self, client, app, get_csrf_token):
        with app.app_context():
            org = _create_organization("Organizacao Issue 64 Launcher Sem Vazamento")
            user = _create_user("membro.issue64.semvazamento@example.com")
            product = _create_product(
                code="produto-issue64-sem-vazamento",
                url="https://produto-issue64-sem-vazamento.local",
            )
            OrganizationService.add_member(org.id, user.id, "member")
            org_product = _create_org_product(org, product, status="active")
            installation = _create_installation(org_product)
            secret = InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)

            credential = OrganizationProductInstallationCredential.query.filter_by(
                organization_product_installation_id=installation.id
            ).first()
            secret_hash = credential.secret_hash
            public_id_str = str(installation.public_id)

        _login(client, get_csrf_token, "membro.issue64.semvazamento@example.com")
        response = client.get("/")
        assert response.status_code == 200
        html = response.data.decode("utf-8")

        assert secret not in html
        assert secret_hash not in html
        assert public_id_str not in html


class _StrRaisesRuntimeError:
    """Objeto sintético (Achado B1 da revisão): `__str__` levanta uma
    exceção comum derivada de `Exception` (nunca `BaseException`) -
    usado só para provar que `_normalize_installation_id`/
    `_normalize_public_id_for_auth` nunca deixam uma exceção arbitrária
    escapar quando a falha ocorre em `str(value)`, e não na própria
    conversão UUID. Nunca impresso nem serializado em nenhum teste ou
    relatório - a falha em `__str__` é exatamente o que garante isso."""

    def __str__(self):
        raise RuntimeError("falha sintetica proposital em __str__ (Achado B1)")


class TestB1BrokenStrObjectNeverPropagatesOrLeaks:
    """Achado B1 (revisão técnica): a normalização de `public_id`/
    `installation_id` deve capturar QUALQUER `Exception` levantada
    durante `str(value)` ou a conversão UUID em si - não só as
    exceções que o próprio `uuid.UUID(...)` levanta."""

    def test_authenticate_installation_with_broken_public_id_returns_none(self, app):
        with app.app_context():
            _installation, secret = _installation_with_credential()

            result = InstallationCredentialService.authenticate_installation(
                _StrRaisesRuntimeError(), secret
            )
            assert result is None

    def test_authenticate_installation_dummy_comparison_executed_for_broken_public_id(self, app, monkeypatch):
        with app.app_context():
            calls = []
            original = hmac.compare_digest

            def _spy(a, b):
                calls.append((a, b))
                return original(a, b)

            monkeypatch.setattr(credential_service_module.hmac, "compare_digest", _spy)

            InstallationCredentialService.authenticate_installation(
                _StrRaisesRuntimeError(), "qualquer-coisa-valida"
            )

            assert len(calls) == 1

    def test_authenticate_installation_never_queries_credentials_for_broken_public_id(self, app, monkeypatch):
        with app.app_context():
            queried = []
            original = InstallationCredentialService._accepted_credentials_query

            def _spy(installation_id, now):
                queried.append(installation_id)
                return original(installation_id, now)

            monkeypatch.setattr(
                InstallationCredentialService, "_accepted_credentials_query", staticmethod(_spy)
            )

            InstallationCredentialService.authenticate_installation(
                _StrRaisesRuntimeError(), "qualquer-coisa-valida"
            )

            # Nenhuma credencial candidata é sequer consultada quando o
            # `public_id` não normaliza - o curto-circuito ocorre antes
            # da busca de instalação/credenciais.
            assert queried == []

    def test_authenticate_installation_no_mutation_or_audit_for_broken_public_id(self, app):
        with app.app_context():
            _installation, secret = _installation_with_credential()
            audit_before = AuditLog.query.count()
            credentials_before = OrganizationProductInstallationCredential.query.count()

            InstallationCredentialService.authenticate_installation(_StrRaisesRuntimeError(), secret)

            assert AuditLog.query.count() == audit_before
            assert OrganizationProductInstallationCredential.query.count() == credentials_before

    def test_issue_credential_with_broken_installation_id_raises_domain_error(self, app):
        with app.app_context():
            with pytest.raises(InstallationCredentialError) as exc_info:
                InstallationCredentialService.issue_credential(_StrRaisesRuntimeError(), actor_user_id=None)
            assert not isinstance(exc_info.value, InstallationCredentialOperationError)

    def test_rotate_credential_with_broken_installation_id_raises_domain_error(self, app):
        with app.app_context():
            with pytest.raises(InstallationCredentialError) as exc_info:
                InstallationCredentialService.rotate_credential(_StrRaisesRuntimeError(), actor_user_id=None)
            assert not isinstance(exc_info.value, InstallationCredentialOperationError)

    def test_revoke_credential_with_broken_installation_id_raises_domain_error(self, app):
        with app.app_context():
            with pytest.raises(InstallationCredentialError) as exc_info:
                InstallationCredentialService.revoke_credential(_StrRaisesRuntimeError(), actor_user_id=None)
            assert not isinstance(exc_info.value, InstallationCredentialOperationError)

    def test_administrative_operations_never_reach_lock_for_broken_installation_id(self, app, monkeypatch):
        with app.app_context():
            calls = []
            original = InstallationCredentialService._lock_installation_row

            def _spy(installation_id):
                calls.append(installation_id)
                return original(installation_id)

            monkeypatch.setattr(InstallationCredentialService, "_lock_installation_row", staticmethod(_spy))

            for operation in (
                InstallationCredentialService.issue_credential,
                InstallationCredentialService.rotate_credential,
                InstallationCredentialService.revoke_credential,
            ):
                with pytest.raises(InstallationCredentialError):
                    operation(_StrRaisesRuntimeError(), actor_user_id=None)

            assert calls == []

    def test_administrative_operations_never_mutate_database_for_broken_installation_id(self, app):
        with app.app_context():
            audit_before = AuditLog.query.count()
            credentials_before = OrganizationProductInstallationCredential.query.count()

            for operation in (
                InstallationCredentialService.issue_credential,
                InstallationCredentialService.rotate_credential,
                InstallationCredentialService.revoke_credential,
            ):
                with pytest.raises(InstallationCredentialError):
                    operation(_StrRaisesRuntimeError(), actor_user_id=None)

            assert AuditLog.query.count() == audit_before
            assert OrganizationProductInstallationCredential.query.count() == credentials_before

    def test_administrative_operations_error_message_stays_generic_for_broken_installation_id(self, app):
        with app.app_context():
            for operation in (
                InstallationCredentialService.issue_credential,
                InstallationCredentialService.rotate_credential,
                InstallationCredentialService.revoke_credential,
            ):
                with pytest.raises(InstallationCredentialError) as exc_info:
                    operation(_StrRaisesRuntimeError(), actor_user_id=None)
                # O valor bruto nunca pôde nem ser convertido para
                # string (é exatamente por isso que o objeto sintético
                # existe) - a mensagem permanece a mesma usada para
                # qualquer outro identificador inválido, nunca uma
                # representação do objeto recebido ou do RuntimeError
                # original levantado por `__str__`.
                assert str(exc_info.value) == "Identificador de instalação inválido."
