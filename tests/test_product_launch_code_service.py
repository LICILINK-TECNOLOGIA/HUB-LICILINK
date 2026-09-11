"""Issue #66: service de emissão do código de lançamento
(`ProductLaunchCodeService.issue_launch_code`), com revalidação integral
da cadeia `User -> OrganizationMember -> Organization -> OrganizationProduct
-> OrganizationProductInstallation` (Issues #63/#64/#65). Cobre
exclusivamente o service - geração/hash, revalidação, TTL, política de
múltiplos códigos, atomicidade/auditoria e taxonomia de exceções. Nenhuma
rota, endpoint, UI ou consumo é exercitado aqui (não existem nesta Issue),
por isso não há teste manual - tudo é validado via service + banco
efêmero (fixture `app`)."""
import hashlib
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    AuditLog,
    Organization,
    OrganizationMember,
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
from app.services.product_launch_code_service import (
    ProductLaunchCodeError,
    ProductLaunchCodeIssuance,
    ProductLaunchCodeOperationError,
    ProductLaunchCodeService,
)
import app.services.product_launch_code_service as product_launch_code_service_module

SYNTHETIC_PASSWORD = "senha-sintetica-issue-66-123"


class _StrRaisesRuntimeError:
    """Objeto sintético (mesmo padrão do Achado B1 da Issue #64): `__str__`
    levanta uma exceção comum derivada de `Exception` (nunca
    `BaseException`) - usado só para provar que a normalização de
    `user_id`/`organization_id` nunca deixa uma exceção arbitrária
    escapar quando a falha ocorre em `str(value)`, não na conversão UUID
    em si. Nunca impresso nem serializado em nenhum teste."""

    def __str__(self):
        raise RuntimeError("falha sintetica proposital em __str__ (Issue #66)")


def _create_organization(is_active=True, legal_name=None):
    org = Organization(
        legal_name=legal_name or f"Organizacao Issue 66 {uuid.uuid4().hex[:8]}",
        is_active=is_active,
    )
    db.session.add(org)
    db.session.commit()
    return org


def _create_product(code=None, url="https://produto-issue-66.local"):
    # `ProductLaunchCodeService` só aceita códigos do catálogo canônico
    # (`STRUCTURAL_PRODUCTS`: 'kalender'/'gedo'/'hunt') - diferente das
    # Issues #63/#64/#65, cujos testes nunca passavam pela validação de
    # produto canônico. O padrão aqui é sempre um código real do
    # catálogo (nunca um código aleatório fora dele).
    product = Product(
        code=code or "gedo",
        name="Produto Issue 66",
        description="Descricao Produto Issue 66",
        url=url,
    )
    db.session.add(product)
    db.session.commit()
    return product


def _create_org_product(org, product, status="active"):
    org_product = OrganizationProduct(organization_id=org.id, product_id=product.id, status=status)
    db.session.add(org_product)
    db.session.commit()
    return org_product


def _create_installation(org_product, url="https://instalacao-issue-66.local", is_active=True):
    installation = OrganizationProductInstallation(
        organization_product_id=org_product.id, url=url, is_active=is_active,
    )
    db.session.add(installation)
    db.session.commit()
    return installation


def _create_user(email=None, is_active=True, verified=True):
    user = User(
        name="Usuario Issue 66",
        email=email or f"usuario.issue66.{uuid.uuid4().hex[:8]}@example.test",
        email_verified_at=datetime.utcnow() if verified else None,
        is_active=is_active,
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


def _build_valid_chain(product_code=None, org_product_status="active"):
    """Monta a cadeia completa e válida (usuário ativo/verificado, vínculo
    `active`, organização ativa, produto canônico com assinatura
    `active`/`trial`, instalação ativa com URL não vazia) - ponto de
    partida de todo teste, mutado conforme o cenário de rejeição."""
    user = _create_user()
    organization = _create_organization(is_active=True)
    membership = OrganizationService.add_member(organization.id, user.id, "member")
    product = _create_product(code=product_code)
    org_product = _create_org_product(organization, product, status=org_product_status)
    installation = _create_installation(org_product, is_active=True)
    return {
        "user": user,
        "organization": organization,
        "membership": membership,
        "product": product,
        "org_product": org_product,
        "installation": installation,
    }


def _issue(chain, **overrides):
    user_id = overrides.get("user_id", chain["user"].id)
    organization_id = overrides.get("organization_id", chain["organization"].id)
    product_code = overrides.get("product_code", chain["product"].code)
    return ProductLaunchCodeService.issue_launch_code(user_id, organization_id, product_code)


def _snapshot_counts():
    return {
        "launch_codes": ProductLaunchCode.query.count(),
        "audit_logs": AuditLog.query.count(),
        "credentials": OrganizationProductInstallationCredential.query.count(),
        "users": User.query.count(),
        "organizations": Organization.query.count(),
        "memberships": OrganizationMember.query.count(),
        "products": Product.query.count(),
        "org_products": OrganizationProduct.query.count(),
        "installations": OrganizationProductInstallation.query.count(),
    }


class TestIssueLaunchCodeSuccess:
    def test_issues_valid_launch_code_and_returns_contracted_format(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            result = _issue(chain)

            assert isinstance(result, ProductLaunchCodeIssuance)
            assert isinstance(result.code, str)
            assert len(result.code) >= 40
            assert isinstance(result.expires_at, datetime)

    def test_code_only_available_from_immediate_return(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            result = _issue(chain)

            launch_code = ProductLaunchCode.query.filter_by(
                organization_product_installation_id=chain["installation"].id
            ).first()
            assert launch_code is not None
            assert launch_code.code_hash != result.code
            assert not hasattr(launch_code, "code")

    def test_code_never_appears_in_repr_str_or_fstring(self, app):
        """Achado A2 da revisão técnica: `ProductLaunchCodeIssuance` é um
        `@dataclass`, cujo `repr()`/`str()` padrão listaria TODOS os
        campos (inclusive `code`) se `field(repr=False)` não estivesse
        aplicado - um `logger.info(result)` ingênuo em uma futura rota
        vazaria o segredo. Este teste nunca imprime o código - só
        verifica sua ausência nas representações textuais do objeto."""
        with app.app_context():
            chain = _build_valid_chain()
            result = _issue(chain)

            representations = [repr(result), str(result), f"{result}"]
            for text in representations:
                assert result.code not in text
            # `destination_url`/`expires_at` continuam visíveis - só
            # `code` é omitido (confirma que não é um `__repr__` vazio
            # ou desabilitado por completo).
            assert result.destination_url in repr(result)

    def test_only_sha256_hash_persisted(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            result = _issue(chain)

            launch_code = ProductLaunchCode.query.filter_by(
                organization_product_installation_id=chain["installation"].id
            ).first()
            assert len(launch_code.code_hash) == 64
            assert launch_code.code_hash == hashlib.sha256(result.code.encode("utf-8")).hexdigest()

    def test_generation_uses_token_urlsafe_with_32_bytes(self, app, monkeypatch):
        with app.app_context():
            chain = _build_valid_chain()
            calls = []
            original = product_launch_code_service_module.secrets.token_urlsafe

            def _spy(n):
                calls.append(n)
                return original(n)

            monkeypatch.setattr(product_launch_code_service_module.secrets, "token_urlsafe", _spy)

            _issue(chain)

            assert calls == [32]

    def test_expires_at_computed_from_utc_and_default_ttl(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            before = datetime.now(timezone.utc)
            result = _issue(chain)
            after = datetime.now(timezone.utc)

            assert before + timedelta(seconds=59) <= result.expires_at <= after + timedelta(seconds=61)

    def test_custom_valid_ttl_is_honored(self, app):
        with app.app_context():
            app.config["PRODUCT_LAUNCH_CODE_TTL_SECONDS"] = 30
            chain = _build_valid_chain()
            before = datetime.now(timezone.utc)
            result = _issue(chain)

            assert before + timedelta(seconds=29) <= result.expires_at <= before + timedelta(seconds=32)

    def test_exact_audit_event_without_sensitive_material(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            audit_before = AuditLog.query.filter_by(
                action="organization_product_installation.launch_code_issued"
            ).count()

            result = _issue(chain)

            logs = AuditLog.query.filter_by(
                action="organization_product_installation.launch_code_issued"
            ).all()
            assert len(logs) == audit_before + 1
            log = logs[-1]
            assert log.user_id == chain["user"].id
            assert log.organization_id == chain["organization"].id
            assert log.resource_type == "organization_product_installation"
            assert log.resource_id == chain["installation"].id

            launch_code = ProductLaunchCode.query.filter_by(
                organization_product_installation_id=chain["installation"].id
            ).first()
            assert log.details == {"launch_code_id": str(launch_code.id)}

            details_str = str(log.details)
            assert result.code not in details_str
            assert launch_code.code_hash not in details_str

    def test_no_side_effects_on_existing_entities(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            before = _snapshot_counts()
            user_updated_at = chain["user"].updated_at
            org_updated_at = chain["organization"].updated_at
            installation_updated_at = chain["installation"].updated_at
            org_product_updated_at = chain["org_product"].updated_at

            _issue(chain)

            after = _snapshot_counts()
            assert after["launch_codes"] == before["launch_codes"] + 1
            assert after["audit_logs"] == before["audit_logs"] + 1
            for key in ("users", "organizations", "memberships", "products", "org_products", "installations", "credentials"):
                assert after[key] == before[key]

            db.session.expire_all()
            assert User.query.get(chain["user"].id).updated_at == user_updated_at
            assert Organization.query.get(chain["organization"].id).updated_at == org_updated_at
            assert OrganizationProductInstallation.query.get(chain["installation"].id).updated_at == installation_updated_at
            assert OrganizationProduct.query.get(chain["org_product"].id).updated_at == org_product_updated_at

    def test_org_product_status_trial_is_accepted(self, app):
        with app.app_context():
            chain = _build_valid_chain(org_product_status="trial")
            result = _issue(chain)
            assert isinstance(result, ProductLaunchCodeIssuance)


class TestIssueLaunchCodeDestinationUrl:
    """Achado M1 da revisão técnica: `destination_url` deve ser
    exatamente a URL da `OrganizationProductInstallation` já resolvida e
    validada pela cadeia - nunca `Product.url`, nunca uma segunda
    consulta, nunca modificada (nem `.strip()`)."""

    def test_destination_url_matches_installation_exactly(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            result = _issue(chain)

            db.session.expire_all()
            reloaded_installation = OrganizationProductInstallation.query.get(
                chain["installation"].id
            )
            launch_code = ProductLaunchCode.query.filter_by(
                organization_product_installation_id=chain["installation"].id
            ).first()

            assert result.destination_url == reloaded_installation.url
            assert launch_code.organization_product_installation_id == chain["installation"].id

    def test_destination_url_with_external_whitespace_returned_byte_for_byte(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            padded_url = "  https://instalacao-com-espacos-issue-66.local  "
            chain["installation"].url = padded_url
            db.session.commit()

            result = _issue(chain)

            # Não vazia (passa na validação, que só verifica `strip() !=
            # ''`) mas retornada exatamente como armazenada - nenhum
            # `.strip()` é aplicado ao valor de retorno.
            assert result.destination_url == padded_url

    def test_destination_url_never_equals_product_url_when_different(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            assert chain["product"].url != chain["installation"].url  # sanidade do setup

            result = _issue(chain)

            assert result.destination_url == chain["installation"].url
            assert result.destination_url != chain["product"].url

    def test_two_organizations_same_product_receive_respective_installation_urls(self, app):
        with app.app_context():
            product = _create_product(code="gedo")

            user_a = _create_user()
            org_a = _create_organization(legal_name="Organizacao A Issue 66")
            OrganizationService.add_member(org_a.id, user_a.id, "member")
            org_product_a = _create_org_product(org_a, product)
            installation_a = _create_installation(org_product_a, url="https://instalacao-org-a-issue66.local")

            user_b = _create_user()
            org_b = _create_organization(legal_name="Organizacao B Issue 66")
            OrganizationService.add_member(org_b.id, user_b.id, "member")
            org_product_b = _create_org_product(org_b, product)
            installation_b = _create_installation(org_product_b, url="https://instalacao-org-b-issue66.local")

            result_a = ProductLaunchCodeService.issue_launch_code(user_a.id, org_a.id, product.code)
            result_b = ProductLaunchCodeService.issue_launch_code(user_b.id, org_b.id, product.code)

            assert result_a.destination_url == installation_a.url
            assert result_b.destination_url == installation_b.url
            assert result_a.destination_url != result_b.destination_url

    def test_no_extra_installation_query_after_resolution(self, app):
        """Confirma, via evento real do driver (`before_cursor_execute`,
        nunca acoplado a qual método/linha Python é chamado
        internamente), que a tabela de instalações é consultada
        exatamente uma vez por emissão bem-sucedida - nunca uma segunda
        vez só para montar `destination_url`."""
        with app.app_context():
            chain = _build_valid_chain()

            statements = []

            def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
                if "organization_product_installations" in statement:
                    statements.append(statement)

            event.listen(db.engine, "before_cursor_execute", _before_cursor_execute)
            try:
                _issue(chain)
            finally:
                event.remove(db.engine, "before_cursor_execute", _before_cursor_execute)

            select_statements = [s for s in statements if s.strip().upper().startswith("SELECT")]
            assert len(select_statements) == 1


class TestIssueLaunchCodeIdNormalization:
    @pytest.mark.parametrize("bad_value", [None, "", "not-a-uuid", 12345, [], {}])
    def test_invalid_user_id_raises_domain_error(self, app, bad_value):
        with app.app_context():
            chain = _build_valid_chain()
            with pytest.raises(ProductLaunchCodeError) as exc_info:
                _issue(chain, user_id=bad_value)
            assert not isinstance(exc_info.value, ProductLaunchCodeOperationError)

    @pytest.mark.parametrize("bad_value", [None, "", "not-a-uuid", 12345, [], {}])
    def test_invalid_organization_id_raises_domain_error(self, app, bad_value):
        with app.app_context():
            chain = _build_valid_chain()
            with pytest.raises(ProductLaunchCodeError) as exc_info:
                _issue(chain, organization_id=bad_value)
            assert not isinstance(exc_info.value, ProductLaunchCodeOperationError)

    def test_user_id_with_broken_str_never_propagates_raw_exception(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            with pytest.raises(ProductLaunchCodeError) as exc_info:
                _issue(chain, user_id=_StrRaisesRuntimeError())
            assert not isinstance(exc_info.value, ProductLaunchCodeOperationError)
            assert "RuntimeError" not in str(exc_info.value)

    def test_organization_id_with_broken_str_never_propagates_raw_exception(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            with pytest.raises(ProductLaunchCodeError) as exc_info:
                _issue(chain, organization_id=_StrRaisesRuntimeError())
            assert not isinstance(exc_info.value, ProductLaunchCodeOperationError)
            assert "RuntimeError" not in str(exc_info.value)

    def test_invalid_ids_never_reach_generation_persistence_or_audit(self, app, monkeypatch):
        with app.app_context():
            chain = _build_valid_chain()
            calls = []
            monkeypatch.setattr(
                product_launch_code_service_module.secrets, "token_urlsafe",
                lambda n: calls.append(n) or "should-not-be-called",
            )
            before = _snapshot_counts()

            for bad_value in (None, "not-a-uuid", _StrRaisesRuntimeError()):
                with pytest.raises(ProductLaunchCodeError):
                    _issue(chain, user_id=bad_value)
                with pytest.raises(ProductLaunchCodeError):
                    _issue(chain, organization_id=bad_value)

            assert calls == []
            assert _snapshot_counts() == before

    def test_invalid_id_message_never_includes_raw_value(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            suspicious_value = "<<valor-bruto-suspeito-nao-deve-vazar>>"
            with pytest.raises(ProductLaunchCodeError) as exc_info:
                _issue(chain, user_id=suspicious_value)
            assert suspicious_value not in str(exc_info.value)


class TestIssueLaunchCodeProductCodeValidation:
    @pytest.mark.parametrize("bad_code", [None, "", 12345, [], {}, "produto-inexistente-nao-canonico"])
    def test_invalid_product_code_raises_domain_error_without_leaking(self, app, bad_code):
        with app.app_context():
            chain = _build_valid_chain()
            with pytest.raises(ProductLaunchCodeError) as exc_info:
                _issue(chain, product_code=bad_code)
            assert not isinstance(exc_info.value, ProductLaunchCodeOperationError)
            if isinstance(bad_code, str) and bad_code:
                assert bad_code not in str(exc_info.value)

    def test_canonical_code_without_bootstrapped_product_rejected(self, app):
        with app.app_context():
            user = _create_user()
            organization = _create_organization()
            OrganizationService.add_member(organization.id, user.id, "member")
            # Nenhum bootstrap executado - nenhum Product canônico existe.
            with pytest.raises(ProductLaunchCodeError):
                ProductLaunchCodeService.issue_launch_code(user.id, organization.id, "gedo")


class TestIssueLaunchCodeChainRejections:
    """Cada teste parte de uma cadeia válida (`_build_valid_chain`) e
    rompe exatamente UM elo - confirma rejeição como `ProductLaunchCodeError`
    (nunca `OperationError`), ausência de `ProductLaunchCode`/`AuditLog`
    novos, e ausência de alteração em qualquer entidade existente."""

    def _assert_clean_rejection(self, app, chain, **overrides):
        before = _snapshot_counts()
        with pytest.raises(ProductLaunchCodeError) as exc_info:
            _issue(chain, **overrides)
        assert not isinstance(exc_info.value, ProductLaunchCodeOperationError)
        assert _snapshot_counts() == before

    def test_user_nonexistent(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            self._assert_clean_rejection(app, chain, user_id=uuid.uuid4())

    def test_user_inactive(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            chain["user"].is_active = False
            db.session.commit()
            self._assert_clean_rejection(app, chain)

    def test_user_unverified(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            chain["user"].email_verified_at = None
            db.session.commit()
            self._assert_clean_rejection(app, chain)

    def test_organization_nonexistent(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            self._assert_clean_rejection(app, chain, organization_id=uuid.uuid4())

    def test_organization_inactive(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            chain["organization"].is_active = False
            db.session.commit()
            self._assert_clean_rejection(app, chain)

    def test_organization_deactivated_between_two_issuances_fails_the_second(self, app):
        with app.app_context():
            chain = _build_valid_chain()

            first = _issue(chain)
            assert isinstance(first, ProductLaunchCodeIssuance)

            chain["organization"].is_active = False
            db.session.commit()

            # Vínculo, assinatura e instalação continuam ativos - só a
            # organização mudou entre as duas chamadas.
            with pytest.raises(ProductLaunchCodeError):
                _issue(chain)

            # O primeiro código emitido continua intacto (nunca revogado
            # retroativamente por uma mudança de estado posterior).
            assert ProductLaunchCode.query.filter_by(
                organization_product_installation_id=chain["installation"].id
            ).count() == 1

    def test_membership_nonexistent(self, app):
        with app.app_context():
            user = _create_user()
            organization = _create_organization()
            product = _create_product()
            org_product = _create_org_product(organization, product)
            installation = _create_installation(org_product)
            chain = {
                "user": user, "organization": organization, "product": product,
                "org_product": org_product, "installation": installation,
            }
            self._assert_clean_rejection(app, chain)

    @pytest.mark.parametrize("status", ["suspended", "removed"])
    def test_membership_not_active(self, app, status):
        with app.app_context():
            chain = _build_valid_chain()
            chain["membership"].status = status
            db.session.commit()
            self._assert_clean_rejection(app, chain)

    def test_org_product_nonexistent(self, app):
        with app.app_context():
            user = _create_user()
            organization = _create_organization()
            OrganizationService.add_member(organization.id, user.id, "member")
            product = _create_product()
            chain = {"user": user, "organization": organization, "product": product}
            self._assert_clean_rejection(app, chain)

    @pytest.mark.parametrize("status", ["inactive", "suspended"])
    def test_org_product_status_not_active_or_trial(self, app, status):
        # Achado B1 da revisão técnica: `'unsubscribed'` foi removido
        # deste parametrize - é só um placeholder calculado por
        # `AccessService.get_organization_products` quando NÃO existe
        # nenhuma linha de `OrganizationProduct` (nunca um valor
        # realmente persistido na coluna `status`); esse cenário (linha
        # ausente) já é coberto separadamente por
        # `test_org_product_nonexistent`. `'inactive'` (valor real de
        # `revoke_product_access`) e `'suspended'` (valor documentado no
        # model, defensivamente aceito pela coluna mesmo sem writer atual)
        # continuam cobertos aqui, por serem estados persistíveis reais.
        with app.app_context():
            chain = _build_valid_chain(org_product_status=status)
            self._assert_clean_rejection(app, chain)

    def test_installation_nonexistent(self, app):
        with app.app_context():
            user = _create_user()
            organization = _create_organization()
            OrganizationService.add_member(organization.id, user.id, "member")
            product = _create_product()
            org_product = _create_org_product(organization, product)
            chain = {
                "user": user, "organization": organization, "product": product,
                "org_product": org_product,
            }
            self._assert_clean_rejection(app, chain)

    def test_installation_resolved_matches_exact_org_product(self, app):
        """Prova positiva (em vez de negativa): com duas assinaturas
        distintas na mesma organização, cada uma com sua própria
        instalação, emitir para o produto A resolve exclusivamente a
        instalação de A - nunca a de B. Como a instalação é sempre
        localizada a partir do `OrganizationProduct.id` (nunca aceita
        como parâmetro do chamador), não existe caminho para confundir
        as duas."""
        with app.app_context():
            user = _create_user()
            organization = _create_organization()
            OrganizationService.add_member(organization.id, user.id, "member")

            product_a = _create_product(code="gedo")
            org_product_a = _create_org_product(organization, product_a)
            installation_a = _create_installation(org_product_a, url="https://instalacao-a.local")

            product_b = _create_product(code="kalender")
            org_product_b = _create_org_product(organization, product_b)
            installation_b = _create_installation(org_product_b, url="https://instalacao-b.local")

            result = ProductLaunchCodeService.issue_launch_code(user.id, organization.id, product_a.code)

            launch_code = ProductLaunchCode.query.filter_by(
                code_hash=hashlib.sha256(result.code.encode("utf-8")).hexdigest()
            ).first()
            assert launch_code.organization_product_installation_id == installation_a.id
            assert launch_code.organization_product_installation_id != installation_b.id

    def test_installation_inactive(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            chain["installation"].is_active = False
            db.session.commit()
            self._assert_clean_rejection(app, chain)

    @pytest.mark.parametrize("bad_url", ["", "   "])
    def test_installation_url_empty_or_whitespace(self, app, bad_url):
        with app.app_context():
            chain = _build_valid_chain()
            chain["installation"].url = bad_url
            db.session.commit()
            self._assert_clean_rejection(app, chain)

    def test_installation_url_never_modified_by_rejected_attempt(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            chain["installation"].url = "   "
            db.session.commit()

            with pytest.raises(ProductLaunchCodeError):
                _issue(chain)

            db.session.expire_all()
            reloaded = OrganizationProductInstallation.query.get(chain["installation"].id)
            assert reloaded.url == "   "

    def test_installation_url_never_modified_by_successful_attempt(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            original_url = chain["installation"].url

            _issue(chain)

            db.session.expire_all()
            reloaded = OrganizationProductInstallation.query.get(chain["installation"].id)
            assert reloaded.url == original_url

    def test_installation_url_none_defensive_branch_rejected(self, app, monkeypatch):
        """Achado B1 da revisão técnica: `installation.url is None` não é
        alcançável via persistência normal (`url` é `nullable=False` -
        um commit com `url=None` violaria a constraint e escondería o
        comportamento testado atrás de uma `IntegrityError` de SETUP,
        nunca do `ProductLaunchCodeError` que este teste precisa provar).
        Testado substituindo apenas o RESULTADO da consulta de
        instalação (um objeto em memória, nunca persistido) - o método
        sob teste (`issue_launch_code`) continua sendo executado de
        verdade, nunca mockado diretamente."""
        with app.app_context():
            chain = _build_valid_chain()
            real_installation = chain["installation"]
            before = _snapshot_counts()

            fake_installation = SimpleNamespace(
                id=real_installation.id,
                is_active=True,
                url=None,
            )

            class _FakeInstallationQuery:
                def filter_by(self, **kwargs):
                    return self

                def first(self):
                    return fake_installation

            monkeypatch.setattr(OrganizationProductInstallation, "query", _FakeInstallationQuery())
            calls = []
            monkeypatch.setattr(
                product_launch_code_service_module.secrets, "token_urlsafe",
                lambda n: calls.append(n) or "should-not-be-called",
            )

            with pytest.raises(ProductLaunchCodeError) as exc_info:
                _issue(chain)
            assert not isinstance(exc_info.value, ProductLaunchCodeOperationError)
            assert calls == []

            # Restaura `OrganizationProductInstallation.query` real antes
            # de verificar o estado persistido - nenhuma gravação de
            # `NULL` jamais foi tentada no banco.
            monkeypatch.undo()

            assert _snapshot_counts() == before
            reloaded = OrganizationProductInstallation.query.get(real_installation.id)
            assert reloaded.url == real_installation.url


class TestIssueLaunchCodeTtlValidation:
    @pytest.mark.parametrize("bad_ttl", [0, -1, -100, 301, 1000, True, False, "60", 60.0, None, [], {}])
    def test_invalid_ttl_rejected_before_generation(self, app, bad_ttl, monkeypatch):
        with app.app_context():
            app.config["PRODUCT_LAUNCH_CODE_TTL_SECONDS"] = bad_ttl
            chain = _build_valid_chain()

            calls = []
            monkeypatch.setattr(
                product_launch_code_service_module.secrets, "token_urlsafe",
                lambda n: calls.append(n) or "should-not-be-called",
            )
            before = _snapshot_counts()

            with pytest.raises(ProductLaunchCodeError) as exc_info:
                _issue(chain)

            assert not isinstance(exc_info.value, ProductLaunchCodeOperationError)
            assert calls == []
            assert _snapshot_counts() == before
            assert str(bad_ttl) not in str(exc_info.value)

    def test_ttl_boundary_values_accepted(self, app):
        with app.app_context():
            # Códigos canônicos distintos por iteração - `Product.code` é
            # único, e as duas iterações compartilham o mesmo banco
            # efêmero (mesmo teste).
            for boundary, product_code in ((1, "gedo"), (300, "kalender")):
                app.config["PRODUCT_LAUNCH_CODE_TTL_SECONDS"] = boundary
                chain = _build_valid_chain(product_code=product_code)
                result = _issue(chain)
                assert isinstance(result, ProductLaunchCodeIssuance)

    def test_non_numeric_ttl_env_value_fails_loading_real_config_module(self):
        """Achado M1 da revisão técnica: o teste anterior só demonstrava
        `int(os.getenv(...))` do Python puro, sem tocar `app/config.py`
        de verdade. Este teste importa o MÓDULO REAL do repositório em um
        SUBPROCESSO isolado (processo novo, `sys.modules` própria, nunca
        compartilhada com o processo de teste principal) com
        `PRODUCT_LAUNCH_CODE_TTL_SECONDS` inválida no ambiente - prova
        que `app/config.py` de fato falha ao carregar, não apenas que o
        Python falharia em tese. `env` é uma cópia de `os.environ` (nunca
        o dicionário original) - o ambiente do processo principal nunca é
        alterado; nada é escrito em disco; o subprocesso sempre termina
        (timeout de segurança) antes do teste retornar, sem processo
        remanescente. `import app.config` nunca chama `create_app()` -
        não inicia servidor nem toca banco.

        Achado B1 residual (revisão técnica seguinte): `app/config.py`
        converte, no corpo de `Config`, VÁRIAS outras variáveis com
        `int(os.getenv(...))` além de `PRODUCT_LAUNCH_CODE_TTL_SECONDS`
        (`VERIFICATION_CODE_TTL`, `VERIFICATION_MAX_ATTEMPTS`,
        `VERIFICATION_RESEND_COOLDOWN`, `VERIFICATION_MAX_RESENDS`,
        `INSTALLATION_CREDENTIAL_ROTATION_GRACE_SECONDS`) - se o
        ambiente REAL de quem executa o teste tivesse, por acidente,
        qualquer uma delas com um valor não numérico, `completed.stderr`
        conteria "ValueError" pelo motivo ERRADO, e o teste passaria sem
        provar nada sobre `PRODUCT_LAUNCH_CODE_TTL_SECONDS` especificamente.
        Corrigido removendo essas outras variáveis do ambiente copiado
        (nunca do `os.environ` real - só da cópia local `env`), deixando
        cada uma cair no próprio valor padrão válido já definido em
        `app/config.py`; e acrescentando uma asserção que só pode passar
        se o valor sintético distintivo desta Issue aparecer no
        traceback - o que só ocorre se `int()` foi de fato chamado sobre
        ELE."""
        repo_root = Path(__file__).resolve().parents[1]
        invalid_ttl_value = "nao-numerico-sintetico-issue-66"

        env = os.environ.copy()
        # Neutraliza as demais configs numéricas de `app/config.py`
        # (mesmo módulo, mesmo import) - remove qualquer valor que o
        # ambiente real do processo de teste já tenha definido para
        # elas, para que SOMENTE `PRODUCT_LAUNCH_CODE_TTL_SECONDS`
        # permaneça inválida; cada uma cai no próprio padrão válido
        # (`600`/`5`/`60`/`5`/`300`) já codificado em `app/config.py`.
        for other_numeric_config_name in (
            "VERIFICATION_CODE_TTL",
            "VERIFICATION_MAX_ATTEMPTS",
            "VERIFICATION_RESEND_COOLDOWN",
            "VERIFICATION_MAX_RESENDS",
            "INSTALLATION_CREDENTIAL_ROTATION_GRACE_SECONDS",
        ):
            env.pop(other_numeric_config_name, None)
        env["PRODUCT_LAUNCH_CODE_TTL_SECONDS"] = invalid_ttl_value
        env["PYTHONPATH"] = str(repo_root)

        completed = subprocess.run(
            [sys.executable, "-c", "import app.config"],
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # A falha é um crash de inicialização (traceback não tratado),
        # não o `ProductLaunchCodeError` curado do service em tempo de
        # execução - por isso, ao contrário das mensagens do service, o
        # traceback padrão do Python pode conter o valor sintético que a
        # causou (comportamento herdado do `int()`, não introduzido por
        # esta Issue).
        assert completed.returncode != 0
        assert "ValueError" in completed.stderr
        # Prova inequívoca: este literal só pode aparecer no traceback
        # se `int(os.getenv('PRODUCT_LAUNCH_CODE_TTL_SECONDS', 60))` foi
        # de fato a conversão que falhou - nenhuma outra config numérica
        # de `app/config.py` poderia produzir este valor específico,
        # mesmo que também estivesse (por acidente) inválida.
        assert invalid_ttl_value in completed.stderr


class TestIssueLaunchCodeMultiplicityAndConcurrency:
    def test_two_sequential_issuances_create_two_distinct_preserved_codes(self, app):
        with app.app_context():
            chain = _build_valid_chain()

            first = _issue(chain)
            second = _issue(chain)

            assert first.code != second.code
            rows = ProductLaunchCode.query.filter_by(
                organization_product_installation_id=chain["installation"].id
            ).all()
            assert len(rows) == 2
            hashes = {r.code_hash for r in rows}
            assert len(hashes) == 2
            assert all(r.consumed_at is None for r in rows)

    def test_multiple_sequential_issuances_both_succeed(self, app):
        """Achado B1 da revisão técnica: renomeado de
        `test_concurrent_style_issuances_both_succeed` - o nome anterior
        podia sugerir concorrência real para quem lesse só a lista de
        testes. São emissões SEQUENCIAIS independentes (SQLite, banco dos
        testes, não executa conexões verdadeiramente concorrentes - mesma
        limitação já documentada nas Issues #63/#64). Não existe nenhuma
        invariante compartilhada protegida por lock que uma corrida real
        poderia violar (múltiplas emissões são explicitamente permitidas,
        Issue #65/#66) - por isso um teste multithread artificial contra
        SQLite não agregaria nenhuma garantia adicional real; este teste
        confirma apenas que a ausência de lock pessimista não impede
        múltiplas emissões "back-to-back" de serem aceitas de forma
        independente, nunca que uma corrida real de duas conexões
        PostgreSQL foi reproduzida."""
        with app.app_context():
            chain = _build_valid_chain()
            results = [_issue(chain) for _ in range(3)]
            assert len({r.code for r in results}) == 3
            assert ProductLaunchCode.query.filter_by(
                organization_product_installation_id=chain["installation"].id
            ).count() == 3


class TestIssueLaunchCodeAtomicityAndRollback:
    def test_code_hash_collision_causes_rollback_and_operation_error(self, app, monkeypatch):
        with app.app_context():
            chain = _build_valid_chain()

            fixed_code = "codigo-sintetico-fixo-para-forcar-colisao-issue-66"
            existing_hash = hashlib.sha256(fixed_code.encode("utf-8")).hexdigest()
            existing = ProductLaunchCode(
                code_hash=existing_hash,
                user_id=chain["user"].id,
                organization_product_installation_id=chain["installation"].id,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
            )
            db.session.add(existing)
            db.session.commit()

            monkeypatch.setattr(
                ProductLaunchCodeService, "_generate_code", staticmethod(lambda: fixed_code)
            )
            audit_before = AuditLog.query.count()

            with pytest.raises(ProductLaunchCodeOperationError) as exc_info:
                _issue(chain)

            assert isinstance(exc_info.value.__cause__, IntegrityError)
            assert ProductLaunchCode.query.filter_by(code_hash=existing_hash).count() == 1
            assert AuditLog.query.count() == audit_before

    def test_rollback_on_flush_failure(self, app, monkeypatch):
        with app.app_context():
            chain = _build_valid_chain()
            before = _snapshot_counts()

            def _raise():
                raise RuntimeError("falha sintetica de flush")
            monkeypatch.setattr(db.session, "flush", _raise)

            with pytest.raises(ProductLaunchCodeOperationError) as exc_info:
                _issue(chain)

            assert isinstance(exc_info.value.__cause__, RuntimeError)

        with app.app_context():
            assert _snapshot_counts() == before

    def test_rollback_on_audit_failure(self, app, monkeypatch):
        with app.app_context():
            chain = _build_valid_chain()
            before = _snapshot_counts()

            def _raise(*args, **kwargs):
                raise RuntimeError("falha sintetica de auditoria")
            monkeypatch.setattr(
                product_launch_code_service_module.AuditService, "log_action", staticmethod(_raise)
            )

            with pytest.raises(ProductLaunchCodeOperationError) as exc_info:
                _issue(chain)

            assert isinstance(exc_info.value.__cause__, RuntimeError)

        with app.app_context():
            assert _snapshot_counts() == before

    def test_rollback_on_commit_failure(self, app, monkeypatch):
        with app.app_context():
            chain = _build_valid_chain()
            before = _snapshot_counts()

            def _raise():
                raise RuntimeError("falha sintetica de commit")
            monkeypatch.setattr(db.session, "commit", _raise)

            with pytest.raises(ProductLaunchCodeOperationError) as exc_info:
                _issue(chain)

            assert isinstance(exc_info.value.__cause__, RuntimeError)

        with app.app_context():
            assert _snapshot_counts() == before

    def test_operational_failure_never_reclassified_as_domain_error(self, app, monkeypatch):
        with app.app_context():
            chain = _build_valid_chain()

            def _raise():
                raise RuntimeError("falha operacional generica")
            monkeypatch.setattr(db.session, "commit", _raise)

            with pytest.raises(ProductLaunchCodeOperationError):
                _issue(chain)


class TestIssueLaunchCodeCompatibility:
    def test_bootstrap_does_not_create_launch_codes(self, app):
        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            assert ProductLaunchCode.query.count() == 0

    def test_credentials_from_issue_64_unaffected(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            secret = InstallationCredentialService.issue_credential(
                chain["installation"].id, actor_user_id=None
            )

            _issue(chain)

            result = InstallationCredentialService.authenticate_installation(
                chain["installation"].public_id, secret
            )
            assert result is not None
            assert OrganizationProductInstallationCredential.query.filter_by(
                organization_product_installation_id=chain["installation"].id
            ).count() == 1

    def test_launcher_unaffected_and_no_code_or_hash_in_html(self, app, client, get_csrf_token):
        with app.app_context():
            user = _create_user("membro.issue66.launcher@example.test")
            organization = _create_organization()
            OrganizationService.add_member(organization.id, user.id, "member")
            product = _create_product(
                code="gedo", url="https://produto-issue66-launcher.local",
            )
            org_product = _create_org_product(organization, product, status="active")
            installation = _create_installation(org_product)

            chain = {
                "user": user, "organization": organization, "product": product,
                "org_product": org_product, "installation": installation,
            }
            result = _issue(chain)
            launch_code = ProductLaunchCode.query.filter_by(
                organization_product_installation_id=installation.id
            ).first()

        _login(client, get_csrf_token, "membro.issue66.launcher@example.test")
        response = client.get("/")
        assert response.status_code == 200
        html = response.data.decode("utf-8")

        assert 'href="https://produto-issue66-launcher.local"' in html
        assert "Acessar Sistema" in html
        assert result.code not in html
        assert launch_code.code_hash not in html

    def test_no_plan_or_quota_field_in_issuance_result(self, app):
        with app.app_context():
            chain = _build_valid_chain()
            result = _issue(chain)
            assert set(result.__dataclass_fields__.keys()) == {"code", "destination_url", "expires_at"}
