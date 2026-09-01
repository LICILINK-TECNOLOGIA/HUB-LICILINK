from datetime import datetime

import pytest

from app.extensions import db
from app.models import (
    Role,
    Product,
    User,
    Organization,
    OrganizationMember,
    Permission,
    ProductPermission,
    OrganizationProduct,
)
from app.services.access_service import AccessService
from app.services.bootstrap_service import (
    BootstrapService,
    StructuralCatalogConflictError,
    STRUCTURAL_ROLES,
    STRUCTURAL_PRODUCTS,
)
from app.services.organization_service import OrganizationService


@pytest.fixture
def cli_runner(app):
    return app.test_cli_runner()


def _canonical_url(app, product_spec):
    return app.config.get(product_spec["url_config_key"])


class TestEmptyDatabaseBootstrap:
    def test_creates_exactly_two_roles_and_three_products(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()

            assert Role.query.count() == 2
            assert Product.query.count() == 3

    def test_created_roles_match_catalog_names_and_descriptions(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()

            for role_spec in STRUCTURAL_ROLES:
                role = Role.query.filter_by(name=role_spec["name"]).first()
                assert role is not None
                assert role.description == role_spec["description"]

    def test_created_products_match_catalog_codes_names_descriptions_and_urls(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()

            for product_spec in STRUCTURAL_PRODUCTS:
                product = Product.query.filter_by(code=product_spec["code"]).first()
                assert product is not None
                assert product.name == product_spec["name"]
                assert product.description == product_spec["description"]
                assert product.url == _canonical_url(app, product_spec)


class TestIdempotency:
    def test_second_execution_creates_no_duplicates(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            BootstrapService.ensure_structural_catalog()

            assert Role.query.count() == 2
            assert Product.query.count() == 3

    def test_partially_seeded_database_creates_only_missing_records(self, app):
        with app.app_context():
            db.session.add(Role(name="owner", description=STRUCTURAL_ROLES[0]["description"]))
            kalender_spec = next(p for p in STRUCTURAL_PRODUCTS if p["code"] == "kalender")
            db.session.add(Product(
                code="kalender",
                name=kalender_spec["name"],
                description=kalender_spec["description"],
                url=_canonical_url(app, kalender_spec),
            ))
            db.session.commit()

            result = BootstrapService.ensure_structural_catalog()

            assert result["created_roles"] == ["member"]
            assert result["created_products"] == ["gedo", "hunt"]
            assert Role.query.count() == 2
            assert Product.query.count() == 3

    def test_existing_compatible_records_are_preserved_unchanged(self, app):
        with app.app_context():
            pre_existing_role = Role(name="owner", description=STRUCTURAL_ROLES[0]["description"])
            db.session.add(pre_existing_role)
            db.session.commit()
            original_id = pre_existing_role.id
            original_created_at = pre_existing_role.created_at

            BootstrapService.ensure_structural_catalog()

            reloaded = Role.query.filter_by(name="owner").first()
            assert reloaded.id == original_id
            assert reloaded.created_at == original_created_at
            assert reloaded.description == STRUCTURAL_ROLES[0]["description"]

    def test_fully_compatible_database_returns_success_without_duplicating(self, app):
        with app.app_context():
            for role_spec in STRUCTURAL_ROLES:
                db.session.add(Role(name=role_spec["name"], description=role_spec["description"]))
            for product_spec in STRUCTURAL_PRODUCTS:
                db.session.add(Product(
                    code=product_spec["code"],
                    name=product_spec["name"],
                    description=product_spec["description"],
                    url=_canonical_url(app, product_spec),
                ))
            db.session.commit()

            result = BootstrapService.ensure_structural_catalog()

            assert result["created_roles"] == []
            assert result["created_products"] == []
            assert set(result["existing_roles"]) == {"owner", "member"}
            assert set(result["existing_products"]) == {"kalender", "gedo", "hunt"}
            assert Role.query.count() == 2
            assert Product.query.count() == 3


class TestUrlResolvedFromRuntimeConfig:
    def test_empty_database_receives_url_from_current_environment_config(self, app):
        with app.app_context():
            app.config["L_KALENDER_URL"] = "https://custom-runtime-value.example/kalender"

            BootstrapService.ensure_structural_catalog()

            product = Product.query.filter_by(code="kalender").first()
            assert product.url == "https://custom-runtime-value.example/kalender"

    def test_diverging_url_on_existing_product_is_treated_as_conflict(self, app):
        with app.app_context():
            kalender_spec = next(p for p in STRUCTURAL_PRODUCTS if p["code"] == "kalender")
            db.session.add(Product(
                code="kalender",
                name=kalender_spec["name"],
                description=kalender_spec["description"],
                url="https://old-url-from-a-previous-environment.example",
            ))
            db.session.commit()

            with pytest.raises(StructuralCatalogConflictError) as exc_info:
                BootstrapService.ensure_structural_catalog()

            assert "kalender" in exc_info.value.conflicting_products


class TestBlockingConflictPolicy:
    def test_role_conflict_blocks_creation_of_missing_product(self, app):
        with app.app_context():
            db.session.add(Role(name="owner", description="Descrição manual divergente."))
            db.session.commit()

            with pytest.raises(StructuralCatalogConflictError):
                BootstrapService.ensure_structural_catalog()

            # Nada foi criado - nem o Role sem conflito (member) nem nenhum
            # dos Products, mesmo estando ausentes e sem conflito próprio.
            assert Role.query.count() == 1
            assert Product.query.count() == 0

    def test_product_conflict_blocks_creation_of_missing_role(self, app):
        with app.app_context():
            db.session.add(Product(
                code="kalender", name="Nome Divergente", description="Desc divergente.", url="http://old.example"
            ))
            db.session.commit()

            with pytest.raises(StructuralCatalogConflictError):
                BootstrapService.ensure_structural_catalog()

            assert Product.query.count() == 1
            assert Role.query.count() == 0

    def test_multiple_conflicts_are_all_detected_before_any_write(self, app):
        with app.app_context():
            db.session.add(Role(name="owner", description="Descrição manual divergente."))
            db.session.add(Product(
                code="kalender", name="Nome Divergente", description="Desc divergente.", url="http://old.example"
            ))
            db.session.commit()

            with pytest.raises(StructuralCatalogConflictError) as exc_info:
                BootstrapService.ensure_structural_catalog()

            assert exc_info.value.conflicting_roles == ["owner"]
            assert exc_info.value.conflicting_products == ["kalender"]
            # Estado inalterado: só os dois registros pré-existentes, nada
            # novo (nem "member", nem "gedo"/"hunt") foi criado.
            assert Role.query.count() == 1
            assert Product.query.count() == 1

    def test_conflicting_record_itself_is_not_modified(self, app):
        with app.app_context():
            divergent_description = "Descrição manual, diferente do catálogo oficial."
            db.session.add(Role(name="owner", description=divergent_description))
            db.session.commit()

            with pytest.raises(StructuralCatalogConflictError):
                BootstrapService.ensure_structural_catalog()

            reloaded = Role.query.filter_by(name="owner").first()
            assert reloaded.description == divergent_description

    def test_error_message_does_not_expose_url_or_description_content(self, app):
        with app.app_context():
            db.session.add(Product(
                code="kalender",
                name="Nome Divergente",
                description="Descrição secreta divergente não deveria vazar.",
                url="https://internal-secret-url.example/should-not-leak",
            ))
            db.session.commit()

            with pytest.raises(StructuralCatalogConflictError) as exc_info:
                BootstrapService.ensure_structural_catalog()

            message = str(exc_info.value)
            assert "internal-secret-url" not in message
            assert "secreta" not in message
            assert "kalender" in message


class TestAtomicityAndRollback:
    def test_failure_during_commit_rolls_back_completely(self, app, monkeypatch):
        def _raise_commit():
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(db.session, "commit", _raise_commit)

        with app.app_context():
            with pytest.raises(ValueError):
                BootstrapService.ensure_structural_catalog()

        # Verificação em sessão limpa (não depende apenas da chamada de
        # rollback ter sido "mockada"/observada): removemos a sessão atual e
        # reconsultamos o mesmo banco para confirmar que nada ficou
        # parcialmente persistido.
        with app.app_context():
            db.session.remove()
            assert Role.query.count() == 0
            assert Product.query.count() == 0

    def test_conflict_rollback_preserves_exact_prior_state_in_new_session(self, app):
        with app.app_context():
            pre_existing = Role(name="owner", description="Descrição manual divergente.")
            db.session.add(pre_existing)
            db.session.commit()
            original_id = pre_existing.id
            original_created_at = pre_existing.created_at
            original_updated_at = pre_existing.updated_at

        with app.app_context():
            with pytest.raises(StructuralCatalogConflictError):
                BootstrapService.ensure_structural_catalog()

        # Sessão limpa e nova consulta - nunca dependendo apenas do mock
        # ou do estado em memória da sessão que levantou o erro.
        with app.app_context():
            db.session.remove()
            assert Role.query.count() == 1
            assert Product.query.count() == 0
            reloaded = Role.query.filter_by(name="owner").first()
            assert reloaded.id == original_id
            assert reloaded.created_at == original_created_at
            assert reloaded.updated_at == original_updated_at
            assert reloaded.description == "Descrição manual divergente."

    def test_exactly_one_commit_on_success(self, app, monkeypatch):
        commit_calls = []
        original_commit = db.session.commit

        def _counting_commit():
            commit_calls.append(1)
            return original_commit()

        monkeypatch.setattr(db.session, "commit", _counting_commit)

        with app.app_context():
            BootstrapService.ensure_structural_catalog()

        assert len(commit_calls) == 1

    def test_zero_commits_on_conflict(self, app, monkeypatch):
        commit_calls = []
        original_commit = db.session.commit

        def _counting_commit():
            commit_calls.append(1)
            return original_commit()

        with app.app_context():
            db.session.add(Role(name="owner", description="Descrição manual divergente."))
            db.session.commit()

        monkeypatch.setattr(db.session, "commit", _counting_commit)

        with app.app_context():
            with pytest.raises(StructuralCatalogConflictError):
                BootstrapService.ensure_structural_catalog()

        assert len(commit_calls) == 0


class TestScopeIsRestrictedToStructuralCatalog:
    def test_does_not_create_unrelated_entities(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()

            assert User.query.count() == 0
            assert Organization.query.count() == 0
            assert OrganizationMember.query.count() == 0
            assert Permission.query.count() == 0
            assert ProductPermission.query.count() == 0

    def test_does_not_grant_any_product_access_to_organization(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()

            assert OrganizationProduct.query.count() == 0


class TestCliCommand:
    def test_command_is_registered(self, app):
        assert "bootstrap-structural-data" in app.cli.commands

    def test_command_runs_successfully_via_cli_runner(self, cli_runner, app):
        result = cli_runner.invoke(args=["bootstrap-structural-data"])

        assert result.exit_code == 0
        with app.app_context():
            assert Role.query.count() == 2
            assert Product.query.count() == 3

    def test_success_output_reports_created_and_existing_without_secrets(self, cli_runner):
        result = cli_runner.invoke(args=["bootstrap-structural-data"])

        assert "owner" in result.output
        assert "member" in result.output
        assert "kalender" in result.output
        assert "gedo" in result.output
        assert "hunt" in result.output
        assert "password" not in result.output.lower()
        assert "senha" not in result.output.lower()
        assert "token" not in result.output.lower()
        # Nenhuma URL completa (protocolo + domínio) deve aparecer na saída.
        assert "http://" not in result.output
        assert "https://" not in result.output

    def test_conflict_returns_nonzero_exit_code(self, cli_runner, app):
        with app.app_context():
            db.session.add(Role(name="owner", description="Descrição manual divergente."))
            db.session.commit()

        result = cli_runner.invoke(args=["bootstrap-structural-data"])

        assert result.exit_code != 0

    def test_conflict_creates_no_records_at_all(self, cli_runner, app):
        with app.app_context():
            db.session.add(Role(name="owner", description="Descrição manual divergente."))
            db.session.commit()

        cli_runner.invoke(args=["bootstrap-structural-data"])

        with app.app_context():
            db.session.remove()
            assert Role.query.count() == 1
            assert Product.query.count() == 0

    def test_conflict_output_identifies_conflicting_records_without_secrets(self, cli_runner, app):
        with app.app_context():
            db.session.add(Product(
                code="kalender",
                name="Nome Divergente",
                description="Descrição secreta divergente não deveria vazar.",
                url="https://internal-secret-url.example/should-not-leak",
            ))
            db.session.commit()

        result = cli_runner.invoke(args=["bootstrap-structural-data"])

        assert "kalender" in result.output
        assert "internal-secret-url" not in result.output
        assert "secreta" not in result.output
        assert "http://" not in result.output
        assert "https://" not in result.output
        assert "password" not in result.output.lower()
        assert "senha" not in result.output.lower()
        assert "token" not in result.output.lower()

    def test_second_invocation_via_cli_runner_creates_no_duplicates(self, cli_runner, app):
        cli_runner.invoke(args=["bootstrap-structural-data"])
        cli_runner.invoke(args=["bootstrap-structural-data"])

        with app.app_context():
            assert Role.query.count() == 2
            assert Product.query.count() == 3


class TestCanonicalProductCatalogShape:
    """Issue #27: STRUCTURAL_PRODUCTS é a única fonte canônica dos códigos
    de produto persistidos - 'kalender', 'gedo', 'hunt', sempre sem o
    prefixo comercial 'L-' (que pertence só a Product.name e às variáveis
    L_*_URL). Estes testes validam o formato do catálogo em si, sem
    depender de banco/bootstrap."""

    def test_canonical_codes_are_exactly_kalender_gedo_hunt(self):
        codes = {spec["code"] for spec in STRUCTURAL_PRODUCTS}
        assert codes == {"kalender", "gedo", "hunt"}

    def test_canonical_codes_are_unique(self):
        codes = [spec["code"] for spec in STRUCTURAL_PRODUCTS]
        assert len(codes) == len(set(codes))

    def test_no_canonical_code_uses_l_prefix(self):
        for spec in STRUCTURAL_PRODUCTS:
            assert not spec["code"].startswith("l-")

    def test_every_product_spec_has_the_required_fields(self):
        required_keys = {"code", "name", "description", "url_config_key"}
        for spec in STRUCTURAL_PRODUCTS:
            assert required_keys.issubset(spec.keys())

    def test_url_config_key_matches_expected_environment_variable_per_product(self):
        expected = {
            "kalender": "L_KALENDER_URL",
            "gedo": "L_GEDO_URL",
            "hunt": "L_HUNT_URL",
        }
        for spec in STRUCTURAL_PRODUCTS:
            assert spec["url_config_key"] == expected[spec["code"]]


class TestAccessServiceReflectsCanonicalCatalog:
    """Issue #27: AccessService/dashboard nunca redeclaram uma lista própria
    de produtos - leem diretamente os registros que o bootstrap já criou a
    partir de STRUCTURAL_PRODUCTS. Estes testes provam que os três produtos
    estruturais (incluindo hunt) chegam corretamente ao Launcher, com URL
    vinda da configuração, e que uma concessão para um produto nunca afeta
    os demais nem duplica registros."""

    SYNTHETIC_PASSWORD = "senha-sintetica-issue-27-123"

    def _create_org_with_member(self):
        organization = Organization(legal_name="Organizacao Catalogo Issue 27")
        db.session.add(organization)
        db.session.flush()

        user = User(
            name="Usuario Catalogo Issue 27",
            email="usuario.catalogo.issue27@example.com",
            email_verified_at=datetime.utcnow(),
        )
        user.set_password(self.SYNTHETIC_PASSWORD)
        db.session.add(user)
        db.session.commit()

        OrganizationService.add_member(organization.id, user.id, "member")
        return organization, user

    def test_access_service_returns_exactly_the_three_canonical_products(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            organization, user = self._create_org_with_member()

            items = AccessService.get_organization_products(user.id, organization.id)

            codes = {item["product"].code for item in items}
            assert codes == {"kalender", "gedo", "hunt"}

    def test_hunt_appears_in_access_service_listing(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            organization, user = self._create_org_with_member()

            items = AccessService.get_organization_products(user.id, organization.id)

            hunt_items = [item for item in items if item["product"].code == "hunt"]
            assert len(hunt_items) == 1

    def test_product_urls_in_access_service_come_from_configuration(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            organization, user = self._create_org_with_member()

            items = AccessService.get_organization_products(user.id, organization.id)

            for item in items:
                spec = next(s for s in STRUCTURAL_PRODUCTS if s["code"] == item["product"].code)
                assert item["product"].url == _canonical_url(app, spec)

    def test_organization_without_grants_receives_all_three_as_unsubscribed(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            organization, user = self._create_org_with_member()

            items = AccessService.get_organization_products(user.id, organization.id)

            assert len(items) == 3
            for item in items:
                assert item["status"] == "unsubscribed"
                assert item["has_access"] is False

    def test_grant_to_gedo_does_not_grant_kalender_or_hunt(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            organization, user = self._create_org_with_member()

            AccessService.grant_product_access(organization.id, "gedo", status="active")

            items = AccessService.get_organization_products(user.id, organization.id)
            by_code = {item["product"].code: item for item in items}

            assert by_code["gedo"]["has_access"] is True
            assert by_code["kalender"]["has_access"] is False
            assert by_code["hunt"]["has_access"] is False

    def test_existing_organization_product_keeps_same_id_and_status_after_repeated_bootstrap(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            organization, user = self._create_org_with_member()

            org_product = AccessService.grant_product_access(organization.id, "gedo", status="active")
            original_id = org_product.id
            original_product_id = org_product.product_id
            original_status = org_product.status
            organization_id = organization.id

            # Uma segunda execução do bootstrap (idempotente) não deve
            # tocar em nenhuma concessão já existente.
            BootstrapService.ensure_structural_catalog()

            db.session.remove()
            reloaded = OrganizationProduct.query.filter_by(organization_id=organization_id).first()
            assert reloaded.id == original_id
            assert reloaded.product_id == original_product_id
            assert reloaded.status == original_status

    def test_no_duplicate_products_after_bootstrap_and_grant_cycle(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            organization, user = self._create_org_with_member()

            AccessService.grant_product_access(organization.id, "kalender", status="active")
            AccessService.grant_product_access(organization.id, "gedo", status="trial")
            BootstrapService.ensure_structural_catalog()

            for code in ("kalender", "gedo", "hunt"):
                assert Product.query.filter_by(code=code).count() == 1
