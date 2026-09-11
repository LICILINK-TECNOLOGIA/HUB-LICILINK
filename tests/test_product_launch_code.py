"""Issue #65: registro persistido de código de lançamento (`ProductLaunchCode`)
- fundação estrutural para a autorização efêmera de uso único da federação
(Issue #62), depois de #63 (instalação) e #64 (credencial por instalação).
Cobre exclusivamente model e migration - geração, hashing, TTL, emissão,
revalidação e consumo atômico pertencem a um service futuro, por isso não
há teste HTTP, de expiração dinâmica ou de consumo aqui, nem teste manual
(não existe rota/UI/endpoint nesta Issue)."""
import hashlib
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
    ProductLaunchCode,
    User,
)
from app.services.bootstrap_service import BootstrapService
from app.services.installation_credential_service import InstallationCredentialService
from app.services.organization_service import OrganizationService

SYNTHETIC_PASSWORD = "senha-sintetica-issue-65-123"


def _synthetic_code_hash():
    """Hash sintético no formato correto (64 caracteres hex, como um SHA-256
    real) - nunca deriva de um código de lançamento real (que nem existe
    nesta Issue, puramente estrutural). Cada chamada gera um valor novo a
    partir de um UUID aleatório, só para preencher `code_hash` em teste."""
    return hashlib.sha256(f"codigo-sintetico-issue-65-{uuid.uuid4()}".encode()).hexdigest()


def _create_organization(legal_name=None):
    org = Organization(legal_name=legal_name or f"Organizacao Issue 65 {uuid.uuid4().hex[:8]}")
    db.session.add(org)
    db.session.commit()
    return org


def _create_product(code=None, name="Produto Issue 65", url="https://produto-issue-65.local"):
    product = Product(code=code or f"produto-issue65-{uuid.uuid4().hex[:8]}", name=name,
                       description=f"Descricao {name}", url=url)
    db.session.add(product)
    db.session.commit()
    return product


def _create_org_product(org, product, status="active"):
    org_product = OrganizationProduct(organization_id=org.id, product_id=product.id, status=status)
    db.session.add(org_product)
    db.session.commit()
    return org_product


def _create_installation(org_product, url="https://instalacao-issue-65.local", is_active=True):
    installation = OrganizationProductInstallation(
        organization_product_id=org_product.id, url=url, is_active=is_active,
    )
    db.session.add(installation)
    db.session.commit()
    return installation


def _new_installation(is_active=True, org_product_status="active"):
    """Monta organização + produto + org_product + instalação - sem
    nenhum código de lançamento ainda."""
    org = _create_organization()
    product = _create_product()
    org_product = _create_org_product(org, product, status=org_product_status)
    return _create_installation(org_product, is_active=is_active)


def _create_user(email=None, *, name="Usuario Issue 65"):
    user = User(
        name=name,
        email=email or f"usuario.issue65.{uuid.uuid4().hex[:8]}@example.test",
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


def _create_launch_code(user=None, installation=None, expires_at=None, consumed_at=None, code_hash=None):
    """Conveniência: monta usuário + instalação (a menos que já fornecidos)
    e persiste um `ProductLaunchCode` com um hash sintético, sem nenhum
    código em claro em nenhum momento."""
    if user is None:
        user = _create_user()
    if installation is None:
        installation = _new_installation()
    launch_code = ProductLaunchCode(
        code_hash=code_hash or _synthetic_code_hash(),
        user_id=user.id,
        organization_product_installation_id=installation.id,
        expires_at=expires_at or (datetime.now(timezone.utc) + timedelta(seconds=60)),
        consumed_at=consumed_at,
    )
    db.session.add(launch_code)
    db.session.commit()
    return launch_code, user, installation


class TestProductLaunchCodeStructure:
    def test_creates_valid_launch_code(self, app):
        with app.app_context():
            launch_code, user, installation = _create_launch_code()

            reloaded = ProductLaunchCode.query.get(launch_code.id)
            assert reloaded is not None
            assert reloaded.user_id == user.id
            assert reloaded.organization_product_installation_id == installation.id
            assert reloaded.consumed_at is None

    def test_field_types(self, app):
        with app.app_context():
            launch_code, _user, _installation = _create_launch_code()

            assert isinstance(launch_code.id, uuid.UUID)
            assert isinstance(launch_code.user_id, uuid.UUID)
            assert isinstance(launch_code.organization_product_installation_id, uuid.UUID)
            assert isinstance(launch_code.code_hash, str)
            assert len(launch_code.code_hash) == 64
            # `DateTime(timezone=True)` é declarado no model/migration para
            # ambos os campos (mesmo padrão de `OrganizationProductInstallationCredential`),
            # mas o driver SQLite dos testes sempre devolve um valor naive
            # após o commit (mesma limitação já documentada nas Issues
            # #63/#64) - por isso o tipo é verificado, não a presença de
            # `tzinfo`, que é preservada de verdade apenas no PostgreSQL.
            assert isinstance(launch_code.expires_at, datetime)

    def test_code_hash_required(self, app):
        with app.app_context():
            user = _create_user()
            installation = _new_installation()
            launch_code = ProductLaunchCode(
                code_hash=None,
                user_id=user.id,
                organization_product_installation_id=installation.id,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
            )
            db.session.add(launch_code)
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_code_hash_unique(self, app):
        with app.app_context():
            shared_hash = _synthetic_code_hash()
            _create_launch_code(code_hash=shared_hash)

            user_b = _create_user()
            installation_b = _new_installation()
            duplicate = ProductLaunchCode(
                code_hash=shared_hash,
                user_id=user_b.id,
                organization_product_installation_id=installation_b.id,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
            )
            db.session.add(duplicate)
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_no_plaintext_code_column(self, app):
        with app.app_context():
            column_names = {c.name for c in ProductLaunchCode.__table__.columns}
            # Comparação por nome EXATO - nunca substring (`code_hash`
            # contém a substring "code", mas não é a coluna proibida).
            forbidden_exact_names = {'code', 'plaintext_code', 'raw_code', 'clear_code', 'secret', 'token'}
            assert column_names.isdisjoint(forbidden_exact_names)
            assert 'code_hash' in column_names
            assert not hasattr(ProductLaunchCode, 'code')
            assert not hasattr(ProductLaunchCode, 'plaintext_code')

    def test_expires_at_required(self, app):
        with app.app_context():
            user = _create_user()
            installation = _new_installation()
            launch_code = ProductLaunchCode(
                code_hash=_synthetic_code_hash(),
                user_id=user.id,
                organization_product_installation_id=installation.id,
                expires_at=None,
            )
            db.session.add(launch_code)
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_consumed_at_initially_null(self, app):
        with app.app_context():
            launch_code, _user, _installation = _create_launch_code()
            assert launch_code.consumed_at is None

    def test_can_persist_consumed_at(self, app):
        with app.app_context():
            launch_code, _user, _installation = _create_launch_code()
            consumed_instant = datetime.now(timezone.utc)

            launch_code.consumed_at = consumed_instant
            db.session.commit()

            db.session.expire_all()
            reloaded = ProductLaunchCode.query.get(launch_code.id)
            assert reloaded.consumed_at is not None

    def test_timestamps_present(self, app):
        with app.app_context():
            launch_code, _user, _installation = _create_launch_code()
            assert launch_code.created_at is not None
            assert launch_code.updated_at is not None

    def test_state_round_trip_after_expire_all(self, app):
        with app.app_context():
            launch_code, user, installation = _create_launch_code()
            launch_code_id = launch_code.id
            expires_at = launch_code.expires_at

            db.session.expire_all()

            reloaded = ProductLaunchCode.query.get(launch_code_id)
            assert reloaded.user_id == user.id
            assert reloaded.organization_product_installation_id == installation.id
            assert reloaded.expires_at == expires_at
            assert reloaded.consumed_at is None


class TestProductLaunchCodeRelationships:
    def test_launch_code_to_user(self, app):
        with app.app_context():
            launch_code, user, _installation = _create_launch_code()
            db.session.expire_all()

            reloaded = ProductLaunchCode.query.get(launch_code.id)
            assert reloaded.user is not None
            assert reloaded.user.id == user.id

    def test_user_to_launch_codes(self, app):
        with app.app_context():
            user = _create_user()
            installation = _new_installation()
            _create_launch_code(user=user, installation=installation)
            _create_launch_code(user=user, installation=_new_installation())

            db.session.expire_all()
            reloaded_user = User.query.get(user.id)
            assert len(reloaded_user.launch_codes) == 2

    def test_launch_code_to_installation(self, app):
        with app.app_context():
            launch_code, _user, installation = _create_launch_code()
            db.session.expire_all()

            reloaded = ProductLaunchCode.query.get(launch_code.id)
            assert reloaded.organization_product_installation is not None
            assert reloaded.organization_product_installation.id == installation.id

    def test_installation_to_launch_codes(self, app):
        with app.app_context():
            installation = _new_installation()
            _create_launch_code(installation=installation)
            _create_launch_code(installation=installation)

            db.session.expire_all()
            reloaded_installation = OrganizationProductInstallation.query.get(installation.id)
            assert len(reloaded_installation.launch_codes) == 2

    def test_physical_deletion_of_user_cascades_to_launch_codes(self, app):
        with app.app_context():
            launch_code, user, _installation = _create_launch_code()
            assert ProductLaunchCode.query.count() == 1

            db.session.delete(User.query.get(user.id))
            db.session.commit()

            assert ProductLaunchCode.query.count() == 0

    def test_physical_deletion_of_installation_cascades_to_launch_codes(self, app):
        with app.app_context():
            launch_code, _user, installation = _create_launch_code()
            assert ProductLaunchCode.query.count() == 1

            db.session.delete(OrganizationProductInstallation.query.get(installation.id))
            db.session.commit()

            assert ProductLaunchCode.query.count() == 0

    def test_physical_deletion_of_installation_cascades_to_both_credential_and_launch_code(self, app):
        """Achado B1 da revisão técnica da Issue #65: os dois relacionamentos
        `cascade="all, delete-orphan"` de `OrganizationProductInstallation`
        (`credentials`, Issue #64, e `launch_codes`, Issue #65) são
        independentes um do outro - este teste confirma que ambos coexistem
        e são corretamente removidos juntos na exclusão física da MESMA
        instalação, sem qualquer interferência entre eles, e sem afetar as
        entidades pai que nunca deveriam ser apagadas nesta operação."""
        with app.app_context():
            installation = _new_installation()
            InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)
            launch_code, user, _installation = _create_launch_code(installation=installation)

            org_product_id = installation.organization_product_id
            organization_id = OrganizationProduct.query.get(org_product_id).organization_id
            product_id = OrganizationProduct.query.get(org_product_id).product_id

            # Estado antes da exclusão: exatamente uma credencial e um
            # código relacionados a esta instalação.
            assert OrganizationProductInstallationCredential.query.filter_by(
                organization_product_installation_id=installation.id
            ).count() == 1
            assert ProductLaunchCode.query.filter_by(
                organization_product_installation_id=installation.id
            ).count() == 1

            db.session.delete(OrganizationProductInstallation.query.get(installation.id))
            db.session.commit()

            # A exclusão concluiu sem exceção (nenhum `pytest.raises` foi
            # necessário) e removeu as duas linhas filhas em cascata.
            assert OrganizationProductInstallationCredential.query.count() == 0
            assert ProductLaunchCode.query.count() == 0
            assert OrganizationProductInstallation.query.count() == 0

            # Nenhuma linha órfã: nenhuma credencial ou código sobrevive
            # referenciando o `id` da instalação já excluída.
            assert OrganizationProductInstallationCredential.query.filter_by(
                organization_product_installation_id=installation.id
            ).count() == 0
            assert ProductLaunchCode.query.filter_by(
                organization_product_installation_id=installation.id
            ).count() == 0

            # Entidades pai preservadas - a exclusão nunca se propaga "para
            # cima" (usuário, organização, produto, OrganizationProduct).
            assert User.query.get(user.id) is not None
            assert Organization.query.get(organization_id) is not None
            assert Product.query.get(product_id) is not None
            assert OrganizationProduct.query.get(org_product_id) is not None

    def test_organization_product_status_change_preserves_launch_codes(self, app):
        with app.app_context():
            installation = _new_installation()
            launch_code, _user, _installation = _create_launch_code(installation=installation)

            org_product = OrganizationProduct.query.get(installation.organization_product_id)
            org_product.status = 'suspended'
            db.session.commit()

            assert ProductLaunchCode.query.count() == 1
            reloaded = ProductLaunchCode.query.get(launch_code.id)
            assert reloaded is not None

    def test_installation_deactivation_preserves_launch_codes(self, app):
        with app.app_context():
            installation = _new_installation()
            launch_code, _user, _installation = _create_launch_code(installation=installation)

            installation.is_active = False
            db.session.commit()

            assert ProductLaunchCode.query.count() == 1
            reloaded = ProductLaunchCode.query.get(launch_code.id)
            assert reloaded is not None

    def test_credential_rotation_and_revocation_preserve_launch_codes(self, app):
        with app.app_context():
            installation = _new_installation()
            launch_code, _user, _installation = _create_launch_code(installation=installation)

            secret = InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)
            InstallationCredentialService.rotate_credential(installation.id, actor_user_id=None)
            InstallationCredentialService.revoke_credential(installation.id, actor_user_id=None)

            assert ProductLaunchCode.query.count() == 1
            reloaded = ProductLaunchCode.query.get(launch_code.id)
            assert reloaded is not None
            assert reloaded.consumed_at is None


class TestProductLaunchCodeMultiplicity:
    def test_two_codes_same_user(self, app):
        with app.app_context():
            user = _create_user()
            _create_launch_code(user=user, installation=_new_installation())
            _create_launch_code(user=user, installation=_new_installation())

            assert ProductLaunchCode.query.filter_by(user_id=user.id).count() == 2

    def test_two_codes_same_installation(self, app):
        with app.app_context():
            installation = _new_installation()
            _create_launch_code(user=_create_user(), installation=installation)
            _create_launch_code(user=_create_user(), installation=installation)

            assert ProductLaunchCode.query.filter_by(
                organization_product_installation_id=installation.id
            ).count() == 2

    def test_two_codes_same_user_and_installation(self, app):
        with app.app_context():
            user = _create_user()
            installation = _new_installation()
            _create_launch_code(user=user, installation=installation)
            _create_launch_code(user=user, installation=installation)

            assert ProductLaunchCode.query.filter_by(
                user_id=user.id, organization_product_installation_id=installation.id,
            ).count() == 2

    def test_codes_for_different_users(self, app):
        with app.app_context():
            installation = _new_installation()
            _code_a, user_a, _i = _create_launch_code(installation=installation)
            _code_b, user_b, _i = _create_launch_code(installation=installation)

            assert user_a.id != user_b.id
            assert ProductLaunchCode.query.count() == 2

    def test_codes_for_different_installations(self, app):
        with app.app_context():
            user = _create_user()
            _code_a, _u, installation_a = _create_launch_code(user=user, installation=_new_installation())
            _code_b, _u, installation_b = _create_launch_code(user=user, installation=_new_installation())

            assert installation_a.id != installation_b.id
            assert ProductLaunchCode.query.count() == 2


class TestProductLaunchCodeIndexesAndMigration:
    def test_unique_constraint_enforced_at_db_level(self, app):
        with app.app_context():
            shared_hash = _synthetic_code_hash()
            first_user = _create_user()
            first_installation = _new_installation()
            first = ProductLaunchCode(
                code_hash=shared_hash,
                user_id=first_user.id,
                organization_product_installation_id=first_installation.id,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
            )
            db.session.add(first)
            db.session.commit()

            second_user = _create_user()
            second_installation = _new_installation()
            second = ProductLaunchCode(
                code_hash=shared_hash,
                user_id=second_user.id,
                organization_product_installation_id=second_installation.id,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
            )
            db.session.add(second)
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()

            assert ProductLaunchCode.query.filter_by(code_hash=shared_hash).count() == 1

    def test_expected_indexes_present(self, app):
        with app.app_context():
            index_columns = {
                tuple(sorted(c.name for c in idx.columns)): idx.unique
                for idx in ProductLaunchCode.__table__.indexes
            }
            assert index_columns.get(('code_hash',)) is True
            assert ('user_id',) in index_columns
            assert ('organization_product_installation_id',) in index_columns
            assert ('expires_at',) in index_columns
            # Nenhum índice duplicado para `code_hash` - um único índice
            # (unique=True) cobre a coluna, nunca dois.
            code_hash_indexes = [
                idx for idx in ProductLaunchCode.__table__.indexes
                if [c.name for c in idx.columns] == ['code_hash']
            ]
            assert len(code_hash_indexes) == 1

    def test_index_names_within_postgres_limit(self, app):
        with app.app_context():
            for idx in ProductLaunchCode.__table__.indexes:
                assert len(idx.name) <= 63, f"{idx.name} excede 63 bytes"

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


class TestProductLaunchCodeCompatibility:
    def test_bootstrap_does_not_create_launch_codes(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            assert ProductLaunchCode.query.count() == 0

    def test_no_audit_created_by_plain_persistence(self, app):
        with app.app_context():
            audit_before = AuditLog.query.count()
            _create_launch_code()
            assert AuditLog.query.count() == audit_before

    def test_no_plan_or_quota_coupling(self, app):
        with app.app_context():
            column_names = {c.name.lower() for c in ProductLaunchCode.__table__.columns}
            forbidden_substrings = ('plan', 'quota', 'price', 'billing')
            for name in column_names:
                for forbidden in forbidden_substrings:
                    assert forbidden not in name, f"coluna {name!r} sugere acoplamento comercial"

    def test_launcher_and_credential_unaffected_by_launch_code_presence(self, app, client, get_csrf_token):
        with app.app_context():
            org = _create_organization("Organizacao Issue 65 Launcher")
            user = _create_user("membro.issue65.launcher@example.test")
            product = _create_product(
                code="produto-issue65-launcher", url="https://produto-issue65-launcher.local",
            )
            OrganizationService.add_member(org.id, user.id, "member")
            org_product = _create_org_product(org, product, status="active")
            installation = _create_installation(org_product)

            secret = InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)
            _create_launch_code(user=user, installation=installation)

            installation_public_id = installation.public_id

        _login(client, get_csrf_token, "membro.issue65.launcher@example.test")
        response = client.get("/")
        assert response.status_code == 200
        html = response.data.decode("utf-8")

        assert 'href="https://produto-issue65-launcher.local"' in html
        assert "Acessar Sistema" in html

        with app.app_context():
            result = InstallationCredentialService.authenticate_installation(installation_public_id, secret)
            assert result is not None
