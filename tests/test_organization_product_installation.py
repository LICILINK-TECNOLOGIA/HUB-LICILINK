"""Issue #63: fundação estrutural do conceito de instalação de produto por
organização (`OrganizationProductInstallation`) - pré-requisito da Issue
#62 (autenticação federada HUB <-> produtos). Puramente estrutural: estes
testes cobrem exclusivamente o model/migration novos (criação,
identificadores, constraints, relacionamentos, cascata, timestamps) e a
preservação total do comportamento atual (`Product.url`, `BootstrapService`,
grant/revoke, launcher) - nenhum destes últimos é alterado por esta Issue,
e nenhum teste aqui assume qualquer validação semântica de URL (HTTPS,
esquemas proibidos, etc.), que fica para uma Issue posterior de service
dedicado."""
import uuid
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Organization, OrganizationProduct, OrganizationProductInstallation, Product, User
from app.services.access_service import AccessService
from app.services.bootstrap_service import BootstrapService
from app.services.organization_service import OrganizationService

SYNTHETIC_PASSWORD = "senha-sintetica-issue-63-123"


def _create_organization(legal_name="Organizacao Issue 63"):
    org = Organization(legal_name=legal_name)
    db.session.add(org)
    db.session.commit()
    return org


def _create_product(code, name="Produto Issue 63", url="https://produto-issue-63.local"):
    product = Product(code=code, name=name, description=f"Descricao {name}", url=url)
    db.session.add(product)
    db.session.commit()
    return product


def _create_org_product(org, product, status="active"):
    org_product = OrganizationProduct(organization_id=org.id, product_id=product.id, status=status)
    db.session.add(org_product)
    db.session.commit()
    return org_product


def _create_installation(org_product, url="https://instalacao-issue-63.local", is_active=True):
    installation = OrganizationProductInstallation(
        organization_product_id=org_product.id, url=url, is_active=is_active,
    )
    db.session.add(installation)
    db.session.commit()
    return installation


def _create_user(email, *, name="Usuario Issue 63"):
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


class TestInstallationCreation:
    def test_creates_valid_installation(self, app):
        with app.app_context():
            org = _create_organization("Organizacao Issue 63 Criacao")
            product = _create_product("produto-issue63-criacao")
            org_product = _create_org_product(org, product)

            installation = _create_installation(org_product, url="https://gedo-prefeitura-a.example.test")

            reloaded = OrganizationProductInstallation.query.get(installation.id)
            assert reloaded is not None
            assert reloaded.organization_product_id == org_product.id
            assert reloaded.url == "https://gedo-prefeitura-a.example.test"
            assert reloaded.is_active is True

    def test_public_id_generated_automatically(self, app):
        with app.app_context():
            org = _create_organization("Organizacao Issue 63 PublicId Auto")
            product = _create_product("produto-issue63-publicid-auto")
            org_product = _create_org_product(org, product)

            installation = _create_installation(org_product)

            assert installation.public_id is not None
            # Gerado automaticamente pelo default do model - não foi
            # atribuído explicitamente em nenhum ponto acima.
            assert isinstance(installation.public_id, uuid.UUID)

    def test_public_id_differs_from_primary_key(self, app):
        with app.app_context():
            org = _create_organization("Organizacao Issue 63 PublicId Diferente")
            product = _create_product("produto-issue63-publicid-diferente")
            org_product = _create_org_product(org, product)

            installation = _create_installation(org_product)

            assert installation.public_id != installation.id


class TestInstallationConstraints:
    def test_public_id_must_be_unique(self, app):
        with app.app_context():
            org_a = _create_organization("Organizacao Issue 63 PublicId Unico A")
            org_b = _create_organization("Organizacao Issue 63 PublicId Unico B")
            product = _create_product("produto-issue63-publicid-unico")
            org_product_a = _create_org_product(org_a, product)
            org_product_b = _create_org_product(org_b, product)

            first = _create_installation(org_product_a)

            duplicate = OrganizationProductInstallation(
                organization_product_id=org_product_b.id,
                public_id=first.public_id,
                url="https://outra-instalacao-issue-63.local",
                is_active=True,
            )
            db.session.add(duplicate)
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()

            assert OrganizationProductInstallation.query.count() == 1

    def test_at_most_one_installation_per_organization_product(self, app):
        with app.app_context():
            org = _create_organization("Organizacao Issue 63 Uma Instalacao")
            product = _create_product("produto-issue63-uma-instalacao")
            org_product = _create_org_product(org, product)

            _create_installation(org_product, url="https://primeira-instalacao-issue-63.local")

            second = OrganizationProductInstallation(
                organization_product_id=org_product.id,
                url="https://segunda-instalacao-issue-63.local",
                is_active=True,
            )
            db.session.add(second)
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()

            assert OrganizationProductInstallation.query.filter_by(
                organization_product_id=org_product.id
            ).count() == 1

    def test_url_is_required(self, app):
        with app.app_context():
            org = _create_organization("Organizacao Issue 63 URL Obrigatoria")
            product = _create_product("produto-issue63-url-obrigatoria")
            org_product = _create_org_product(org, product)

            installation = OrganizationProductInstallation(
                organization_product_id=org_product.id, url=None, is_active=True,
            )
            db.session.add(installation)
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()

            assert OrganizationProductInstallation.query.count() == 0

    def test_is_active_defaults_to_true(self, app):
        with app.app_context():
            org = _create_organization("Organizacao Issue 63 Ativa Por Padrao")
            product = _create_product("produto-issue63-ativa-por-padrao")
            org_product = _create_org_product(org, product)

            installation = OrganizationProductInstallation(
                organization_product_id=org_product.id, url="https://ativa-por-padrao-issue-63.local",
            )
            db.session.add(installation)
            db.session.commit()

            assert installation.is_active is True

    def test_installation_can_be_deactivated(self, app):
        with app.app_context():
            org = _create_organization("Organizacao Issue 63 Desativavel")
            product = _create_product("produto-issue63-desativavel")
            org_product = _create_org_product(org, product)
            installation = _create_installation(org_product)

            installation.is_active = False
            db.session.commit()

            reloaded = OrganizationProductInstallation.query.get(installation.id)
            assert reloaded.is_active is False


class TestMultipleOrganizationsAndProducts:
    def test_two_organizations_have_distinct_installations_of_same_product(self, app):
        with app.app_context():
            org_a = _create_organization("Organizacao Issue 63 Multi A")
            org_b = _create_organization("Organizacao Issue 63 Multi B")
            product = _create_product("produto-issue63-multi-org")

            org_product_a = _create_org_product(org_a, product)
            org_product_b = _create_org_product(org_b, product)

            installation_a = _create_installation(org_product_a, url="https://prefeitura-a-issue-63.local")
            installation_b = _create_installation(org_product_b, url="https://prefeitura-b-issue-63.local")

            assert installation_a.id != installation_b.id
            assert installation_a.url != installation_b.url
            assert installation_a.organization_product_id != installation_b.organization_product_id
            assert OrganizationProductInstallation.query.count() == 2

    def test_different_products_in_same_organization_have_distinct_installations(self, app):
        with app.app_context():
            org = _create_organization("Organizacao Issue 63 Multi Produto")
            product_gedo = _create_product("produto-issue63-multi-gedo", name="GEDO Issue 63")
            product_kalender = _create_product("produto-issue63-multi-kalender", name="Kalender Issue 63")

            org_product_gedo = _create_org_product(org, product_gedo)
            org_product_kalender = _create_org_product(org, product_kalender)

            installation_gedo = _create_installation(org_product_gedo, url="https://gedo-issue-63.local")
            installation_kalender = _create_installation(
                org_product_kalender, url="https://kalender-issue-63.local"
            )

            assert installation_gedo.id != installation_kalender.id
            assert installation_gedo.organization_product_id != installation_kalender.organization_product_id
            assert OrganizationProductInstallation.query.count() == 2


class TestRelationshipBothDirections:
    def test_relationship_accessible_from_both_sides(self, app):
        with app.app_context():
            org = _create_organization("Organizacao Issue 63 Relacionamento")
            product = _create_product("produto-issue63-relacionamento")
            org_product = _create_org_product(org, product)
            installation = _create_installation(org_product)

            db.session.expire_all()

            reloaded_org_product = OrganizationProduct.query.get(org_product.id)
            assert reloaded_org_product.installation is not None
            assert reloaded_org_product.installation.id == installation.id

            reloaded_installation = OrganizationProductInstallation.query.get(installation.id)
            assert reloaded_installation.organization_product is not None
            assert reloaded_installation.organization_product.id == org_product.id


class TestPreservedAcrossAccessLifecycle:
    """Confirma que a instalação sobrevive integralmente ao ciclo de vida
    já existente de OrganizationProduct (Issue #30) - revogação nunca
    apaga a linha, restauração/reconcessão nunca recria."""

    def test_revoking_product_access_preserves_installation(self, app):
        with app.app_context():
            org = _create_organization("Organizacao Issue 63 Revogacao Preserva")
            # grant_product_access/revoke_product_access só aceitam código
            # do catálogo canônico (STRUCTURAL_PRODUCTS) - 'gedo' é usado
            # aqui só como código canônico válido, sem qualquer relação
            # com bootstrap/URL real.
            product = _create_product("gedo", name="L-GeDo Issue 63")
            org_product = _create_org_product(org, product, status="active")
            installation = _create_installation(org_product)
            original_id = installation.id
            original_created_at = installation.created_at

            AccessService.revoke_product_access(org.id, product.code, actor_user_id=None)

            reloaded = OrganizationProductInstallation.query.filter_by(
                organization_product_id=org_product.id
            ).first()
            assert reloaded is not None
            assert reloaded.id == original_id
            assert reloaded.created_at == original_created_at
            assert reloaded.is_active is True
            assert OrganizationProductInstallation.query.count() == 1

    def test_restoring_product_access_reuses_same_installation_without_duplication(self, app):
        with app.app_context():
            org = _create_organization("Organizacao Issue 63 Restauracao Reusa")
            product = _create_product("gedo", name="L-GeDo Issue 63")
            org_product = _create_org_product(org, product, status="active")
            installation = _create_installation(org_product)
            original_id = installation.id

            AccessService.revoke_product_access(org.id, product.code, actor_user_id=None)
            AccessService.grant_product_access(org.id, product.code, actor_user_id=None)

            assert OrganizationProductInstallation.query.count() == 1
            reloaded = OrganizationProductInstallation.query.filter_by(
                organization_product_id=org_product.id
            ).first()
            assert reloaded.id == original_id


class TestReferentialIntegrityAndCascade:
    def test_physical_deletion_of_parent_row_cascades_to_installation(self, app):
        with app.app_context():
            org = _create_organization("Organizacao Issue 63 Cascata")
            product = _create_product("produto-issue63-cascata")
            org_product = _create_org_product(org, product)
            _create_installation(org_product)

            assert OrganizationProductInstallation.query.count() == 1

            # Exclusão física real da linha pai (nunca exercitada pelo
            # fluxo normal da aplicação, que só muda `status` via
            # revoke_product_access) - decisão registrada na Issue #63:
            # isso DEVE remover a instalação em cascata.
            db.session.delete(org_product)
            db.session.commit()

            assert OrganizationProductInstallation.query.count() == 0

    def test_timestamps_are_set_on_creation_and_updated_on_mutation(self, app):
        with app.app_context():
            org = _create_organization("Organizacao Issue 63 Timestamps")
            product = _create_product("produto-issue63-timestamps")
            org_product = _create_org_product(org, product)
            installation = _create_installation(org_product)

            assert installation.created_at is not None
            assert installation.updated_at is not None
            original_created_at = installation.created_at
            original_updated_at = installation.updated_at

            installation.is_active = False
            db.session.commit()

            assert installation.created_at == original_created_at
            assert installation.updated_at >= original_updated_at


class TestNoImplicitCreationOrRegression:
    def test_bootstrap_does_not_create_installations(self, app):
        with app.app_context():
            # A fixture `app` não roda o bootstrap automaticamente (banco
            # de teste começa vazio) - chamamos explicitamente aqui só
            # para confirmar que mesmo uma execução real do bootstrap
            # estrutural não cria nenhuma instalação (ele só toca
            # Role/Product, nunca OrganizationProduct ou esta tabela nova).
            BootstrapService.ensure_structural_catalog()

            assert OrganizationProductInstallation.query.count() == 0

    def test_launcher_unaffected_by_absence_of_installation(self, client, app, get_csrf_token):
        with app.app_context():
            org = _create_organization("Organizacao Issue 63 Launcher Inalterado")
            user = _create_user("membro.issue63.launcher@example.com")
            product = _create_product(
                "produto-issue63-launcher", url="https://produto-issue63-launcher.local",
            )
            OrganizationService.add_member(org.id, user.id, "member")
            _create_org_product(org, product, status="active")
            # Nenhuma OrganizationProductInstallation criada de propósito.

        _login(client, get_csrf_token, "membro.issue63.launcher@example.com")
        response = client.get("/")
        assert response.status_code == 200
        html = response.data.decode("utf-8")

        assert 'href="https://produto-issue63-launcher.local"' in html
        assert "Acessar Sistema" in html
