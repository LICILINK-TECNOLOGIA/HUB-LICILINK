"""Issue #68: `OrganizationProductInstallationService` - validação
central e product-aware da URL de instalação
(`validate_installation_url`) e o ciclo administrativo (criar/atualizar/
ativar/desativar). Cobre exclusivamente o service - a integração HTTP
está em `tests/test_admin_org_product_installation.py`, e a integração
obrigatória com `ProductLaunchCodeService.issue_launch_code` está em
`tests/test_product_launch_code_service.py`."""
import uuid
from datetime import datetime

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
from app.services.installation_credential_service import InstallationCredentialService
from app.services.organization_service import OrganizationService
from app.services.organization_product_installation_service import (
    OrganizationProductInstallationError,
    OrganizationProductInstallationOperationError,
    OrganizationProductInstallationService,
)
import app.services.organization_product_installation_service as opis_module
from app.services.product_launch_code_service import ProductLaunchCodeService

SYNTHETIC_PASSWORD = "senha-sintetica-issue-68-123"


def _create_user(email=None, is_active=True, verified=True):
    user = User(
        name="Usuario Issue 68",
        email=email or f"usuario.issue68.{uuid.uuid4().hex[:8]}@example.test",
        email_verified_at=datetime.utcnow() if verified else None,
        is_active=is_active,
    )
    user.set_password(SYNTHETIC_PASSWORD)
    db.session.add(user)
    db.session.commit()
    return user


def _create_organization(legal_name=None, is_active=True):
    org = Organization(
        legal_name=legal_name or f"Organizacao Issue 68 {uuid.uuid4().hex[:8]}",
        is_active=is_active,
    )
    db.session.add(org)
    db.session.commit()
    return org


def _create_product(code="gedo"):
    product = Product(
        code=code,
        name=f"Produto Issue 68 {code}",
        description=f"Descricao {code}",
        url="https://produto-issue-68.local",
    )
    db.session.add(product)
    db.session.commit()
    return product


def _create_org_product(org, product, status="active"):
    org_product = OrganizationProduct(organization_id=org.id, product_id=product.id, status=status)
    db.session.add(org_product)
    db.session.commit()
    return org_product


def _valid_https_url(host="instalacao-issue-68.local"):
    return f"https://{host}/callback"


# Validador: estrutura/tipo -----------------------------------------------

class TestValidateInstallationUrlStructure:
    @pytest.mark.parametrize("bad_value", [None, 12345, [], {}, True, 3.14])
    def test_non_string_type_rejected(self, app, bad_value):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(bad_value, "gedo")

    def test_empty_string_rejected(self, app):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url("", "gedo")

    def test_whitespace_only_rejected(self, app):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url("   ", "gedo")

    def test_external_whitespace_rejected_never_stripped(self, app):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    "  " + _valid_https_url() + "  ", "gedo"
                )

    def test_exactly_255_characters_accepted(self, app):
        with app.app_context():
            padding = "a" * (255 - len("https://x.local/"))
            url = f"https://x.local/{padding}"
            assert len(url) == 255
            result = OrganizationProductInstallationService.validate_installation_url(url, "gedo")
            assert result == url

    def test_256_characters_rejected(self, app):
        with app.app_context():
            padding = "a" * (256 - len("https://x.local/"))
            url = f"https://x.local/{padding}"
            assert len(url) == 256
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(url, "gedo")

    def test_control_character_rejected(self, app):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    "https://instalacao-issue-68.local/\x01path", "gedo"
                )

    @pytest.mark.parametrize(
        "control_char",
        [
            "\x00",  # C0 - NUL
            "\x09",  # C0 - tab
            "\x1f",  # C0 - último antes de 0x20
            "\x7f",  # DEL
            "\x80",  # C1 - primeiro
            "\x85",  # C1 - NEL (Achado M1-2 da revisão técnica)
            "\x9f",  # C1 - último
        ],
        ids=["C0-NUL", "C0-tab", "C0-0x1f", "DEL", "C1-0x80", "C1-NEL-0x85", "C1-0x9f"],
    )
    def test_control_characters_c0_del_c1_all_rejected(self, app, control_char):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    f"https://instalacao-issue-68.local/{control_char}path", "gedo"
                )

    def test_control_character_check_does_not_reject_adjacent_valid_url(self, app):
        """Achado M1-2 da revisão técnica: a checagem ampliada para C1
        não pode rejeitar por engano uma URL válida comum - prova
        negativa complementar aos casos parametrizados acima."""
        with app.app_context():
            url = "https://instalacao-issue-68.local/caminho-normal"
            result = OrganizationProductInstallationService.validate_installation_url(url, "gedo")
            assert result == url

    @pytest.mark.parametrize(
        "url_with_space_in_host",
        [
            "https://ho st.local/x",
            "https://gedo .local/x",
            "https:// gedo.local/x",
        ],
    )
    def test_space_literal_in_host_rejected(self, app, url_with_space_in_host):
        """Achado M1-3 da revisão técnica: espaço literal dentro do
        hostname já analisado por `urlsplit` - nunca corrigido
        silenciosamente, sempre rejeitado como erro de domínio, em
        qualquer ambiente."""
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError) as exc_info:
                OrganizationProductInstallationService.validate_installation_url(
                    url_with_space_in_host, "gedo"
                )
            assert not isinstance(exc_info.value, OrganizationProductInstallationOperationError)

    def test_space_in_host_rejected_in_production_too(self, app):
        """A checagem de espaço no host é estrutural - não depende de
        `IS_PRODUCTION`, nem se confunde com a política de host por
        produto (que só se aplica em produção)."""
        with app.app_context():
            app.config["IS_PRODUCTION"] = True
            app.config["L_GEDO_URL"] = "https://gedo.exemplo.com"
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    "https://ge do.exemplo.com/x", "gedo"
                )

    def test_space_in_path_is_unaffected_by_host_check(self, app):
        """Fora do escopo desta correção (M1-3 trata apenas do host) -
        registra o comportamento atual sem ampliar o contrato além do
        que a Issue especifica."""
        with app.app_context():
            url = "https://instalacao-issue-68.local/pa th"
            result = OrganizationProductInstallationService.validate_installation_url(url, "gedo")
            assert result == url

    def test_backslash_rejected(self, app):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    "https://instalacao-issue-68.local\\@evil.test", "gedo"
                )

    def test_relative_url_rejected(self, app):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url("/caminho", "gedo")

    def test_missing_host_rejected(self, app):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url("https:///caminho", "gedo")

    @pytest.mark.parametrize("scheme", ["javascript", "data", "file", "ftp", "ws"])
    def test_dangerous_or_unknown_scheme_rejected(self, app, scheme):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    f"{scheme}://instalacao-issue-68.local/x", "gedo"
                )

    def test_userinfo_rejected(self, app):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    "https://usuario:senha@instalacao-issue-68.local/x", "gedo"
                )

    def test_query_string_rejected(self, app):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    "https://instalacao-issue-68.local/x?a=1", "gedo"
                )

    def test_fragment_rejected(self, app):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    "https://instalacao-issue-68.local/x#frag", "gedo"
                )

    def test_non_ascii_host_rejected(self, app):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    "https://gedô.local/x", "gedo"
                )

    def test_path_and_trailing_slash_preserved(self, app):
        with app.app_context():
            url = "https://instalacao-issue-68.local/callback/handoff/"
            result = OrganizationProductInstallationService.validate_installation_url(url, "gedo")
            assert result == url

    def test_valid_url_returned_byte_for_byte(self, app):
        with app.app_context():
            url = _valid_https_url()
            result = OrganizationProductInstallationService.validate_installation_url(url, "gedo")
            assert result == url
            assert result is url or result == url

    def test_invalid_port_rejected(self, app):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    "https://instalacao-issue-68.local:999999/x", "gedo"
                )

    def test_valid_port_accepted(self, app):
        with app.app_context():
            url = "https://instalacao-issue-68.local:8443/x"
            result = OrganizationProductInstallationService.validate_installation_url(url, "gedo")
            assert result == url


# Validador: produto canônico ------------------------------------------------

class TestValidateInstallationUrlProductCode:
    def test_product_outside_catalog_rejected(self, app):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    _valid_https_url(), "produto-inexistente"
                )

    @pytest.mark.parametrize("bad_product", [None, 12345, "", "GEDO"])
    def test_invalid_product_code_type_or_case_rejected(self, app, bad_product):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    _valid_https_url(), bad_product
                )


# Validador: ambiente de desenvolvimento/teste -------------------------------

class TestValidateInstallationUrlDevTestEnvironment:
    @pytest.mark.parametrize(
        "host_in_url",
        ["localhost", "127.0.0.1", "[::1]", "gedo.local", "kalender.local"],
    )
    def test_http_allowed_for_recognized_dev_hosts(self, app, host_in_url):
        # IPv6 loopback precisa da notação com colchetes na URL
        # (`[::1]`) - sem isso, `urlsplit` interpreta o `:` como
        # separador de porta e a URL é sintaticamente inválida (nada a
        # ver com a regra de host reconhecido sendo testada aqui).
        with app.app_context():
            url = f"http://{host_in_url}/callback"
            result = OrganizationProductInstallationService.validate_installation_url(url, "gedo")
            assert result == url

    def test_http_rejected_for_unrecognized_host_in_dev(self, app):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    "http://random-external-host.com/x", "gedo"
                )

    def test_https_accepted_for_any_host_in_dev(self, app):
        with app.app_context():
            url = "https://qualquer-host-https-issue68.example.com/x"
            result = OrganizationProductInstallationService.validate_installation_url(url, "gedo")
            assert result == url

    def test_dev_flexibilization_never_bypasses_structural_checks(self, app):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    "javascript:alert(1)", "gedo"
                )
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    "http://localhost/x?a=1", "gedo"
                )
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    "http://usuario:senha@localhost/x", "gedo"
                )


# Validador: allowlist isolada por produto em produção -----------------------

class TestValidateInstallationUrlProductionAllowlist:
    @pytest.fixture(autouse=True)
    def _production_config(self, app):
        with app.app_context():
            app.config["IS_PRODUCTION"] = True
            app.config["L_GEDO_URL"] = "https://gedo.exemplo.licilink.com.br"
            app.config["L_KALENDER_URL"] = "https://kalender.exemplo.licilink.com.br"
            app.config["L_HUNT_URL"] = "https://hunt.exemplo.licilink.com.br"
        yield

    def test_http_rejected_in_production(self, app):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    "http://gedo.exemplo.licilink.com.br/x", "gedo"
                )

    def test_gedo_accepts_gedo_host(self, app):
        with app.app_context():
            url = "https://gedo.exemplo.licilink.com.br/callback"
            result = OrganizationProductInstallationService.validate_installation_url(url, "gedo")
            assert result == url

    def test_gedo_rejects_kalender_host(self, app):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    "https://kalender.exemplo.licilink.com.br/callback", "gedo"
                )

    def test_gedo_rejects_hunt_host(self, app):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    "https://hunt.exemplo.licilink.com.br/callback", "gedo"
                )

    def test_kalender_accepts_kalender_and_rejects_gedo_and_hunt(self, app):
        with app.app_context():
            url = "https://kalender.exemplo.licilink.com.br/callback"
            result = OrganizationProductInstallationService.validate_installation_url(url, "kalender")
            assert result == url
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    "https://gedo.exemplo.licilink.com.br/callback", "kalender"
                )
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    "https://hunt.exemplo.licilink.com.br/callback", "kalender"
                )

    @pytest.mark.parametrize(
        "product_code,own_host,other_hosts",
        [
            (
                "gedo",
                "https://gedo.exemplo.licilink.com.br/callback",
                [
                    "https://kalender.exemplo.licilink.com.br/callback",
                    "https://hunt.exemplo.licilink.com.br/callback",
                ],
            ),
            (
                "kalender",
                "https://kalender.exemplo.licilink.com.br/callback",
                [
                    "https://gedo.exemplo.licilink.com.br/callback",
                    "https://hunt.exemplo.licilink.com.br/callback",
                ],
            ),
            (
                "hunt",
                "https://hunt.exemplo.licilink.com.br/callback",
                [
                    "https://gedo.exemplo.licilink.com.br/callback",
                    "https://kalender.exemplo.licilink.com.br/callback",
                ],
            ),
        ],
        ids=["gedo", "kalender", "hunt"],
    )
    def test_each_product_accepts_own_host_and_rejects_the_other_two(
        self, app, product_code, own_host, other_hosts
    ):
        """Fecha a lacuna de cobertura simétrica para Hunt (não coberto
        antes como produto primário) e reconfirma GEDO/Kalender de
        forma unificada e parametrizada - usa exclusivamente o
        `url_config_key` do próprio produto em cada caso."""
        with app.app_context():
            result = OrganizationProductInstallationService.validate_installation_url(
                own_host, product_code
            )
            assert result == own_host
            for other_host in other_hosts:
                with pytest.raises(OrganizationProductInstallationError):
                    OrganizationProductInstallationService.validate_installation_url(
                        other_host, product_code
                    )

    def test_each_product_reads_exclusively_its_own_config_key(self, app, monkeypatch):
        """Espiona `current_app.config.get` para provar que apenas a
        chave de configuração do PRÓPRIO produto é lida - nunca as
        outras duas, nunca uma allowlist compartilhada."""
        with app.app_context():
            calls = []
            original_get = app.config.get

            def _spy_get(key, *a, **k):
                calls.append(key)
                return original_get(key, *a, **k)

            monkeypatch.setattr(app.config, "get", _spy_get)

            OrganizationProductInstallationService.validate_installation_url(
                "https://gedo.exemplo.licilink.com.br/callback", "gedo"
            )

            assert "L_GEDO_URL" in calls
            assert "L_KALENDER_URL" not in calls
            assert "L_HUNT_URL" not in calls

    def test_legitimate_subdomain_accepted(self, app):
        with app.app_context():
            url = "https://app.gedo.exemplo.licilink.com.br/callback"
            result = OrganizationProductInstallationService.validate_installation_url(url, "gedo")
            assert result == url

    @pytest.mark.parametrize(
        "malicious_host",
        [
            "naogedo.exemplo.licilink.com.br",
            "gedo.exemplo.licilink.com.br.evil.test",
            "evilgedo.exemplo.licilink.com.br",
        ],
    )
    def test_maliciously_suffixed_host_rejected(self, app, malicious_host):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    f"https://{malicious_host}/callback", "gedo"
                )

    def test_effective_port_match_and_mismatch(self, app):
        with app.app_context():
            # Canônica sem porta explícita = 443 implícito (https).
            ok = OrganizationProductInstallationService.validate_installation_url(
                "https://gedo.exemplo.licilink.com.br:443/callback", "gedo"
            )
            assert ok is not None
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    "https://gedo.exemplo.licilink.com.br:9443/callback", "gedo"
                )

    def test_canonical_ip_literal_accepts_only_exact_match(self, app):
        with app.app_context():
            app.config["L_GEDO_URL"] = "https://203.0.113.10/callback"
            exact = OrganizationProductInstallationService.validate_installation_url(
                "https://203.0.113.10/callback", "gedo"
            )
            assert exact is not None
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    "https://sub.203.0.113.10/callback", "gedo"
                )

    def test_structural_product_without_config_key_rejected(self, app, monkeypatch):
        """Achado M1-1 da revisão técnica, corrigido: um produto
        canônico sem `url_config_key` mapeado (cenário hipotético de
        catálogo incompleto - inatingível hoje, já que
        kalender/gedo/hunt sempre têm a chave) deve falhar fechado como
        `OrganizationProductInstallationError` - a versão anterior deste
        teste exigia `KeyError` (o bug), travando o comportamento
        errado como se fosse o contrato. Corrigido para exigir a
        exceção de domínio correta."""
        with app.app_context():
            monkeypatch.setitem(
                opis_module._CANONICAL_PRODUCTS_BY_CODE,
                "produto-sem-chave",
                {"code": "produto-sem-chave", "name": "X", "description": "X"},
            )
            with pytest.raises(OrganizationProductInstallationError) as exc_info:
                OrganizationProductInstallationService.validate_installation_url(
                    _valid_https_url(), "produto-sem-chave"
                )
            assert not isinstance(exc_info.value, OrganizationProductInstallationOperationError)
            assert "produto-sem-chave" not in str(exc_info.value)

    def test_structural_product_without_config_key_rejected_via_configure_installation(self, app, monkeypatch):
        """Mesmo achado M1-1, agora através de `configure_installation`
        (segundo caminho de chamada) - a falha deve ser erro de
        domínio, nunca `OrganizationProductInstallationOperationError`,
        e não pode escrever nenhuma linha/auditoria."""
        with app.app_context():
            org = _create_organization()
            product = _create_product(code="produto-sem-chave")
            _create_org_product(org, product)
            monkeypatch.setitem(
                opis_module._CANONICAL_PRODUCTS_BY_CODE,
                "produto-sem-chave",
                {"code": "produto-sem-chave", "name": "X", "description": "X"},
            )
            app.config["IS_PRODUCTION"] = True

            with pytest.raises(OrganizationProductInstallationError) as exc_info:
                OrganizationProductInstallationService.configure_installation(
                    org.id, "produto-sem-chave", _valid_https_url(), actor_user_id=None,
                )
            assert not isinstance(exc_info.value, OrganizationProductInstallationOperationError)
            assert OrganizationProductInstallation.query.count() == 0
            assert AuditLog.query.count() == 0

    @pytest.mark.parametrize(
        "bad_canonical",
        [None, "", "   ", 12345, "javascript:alert(1)", "https:///sem-host", "https://user:pass@host.com"],
    )
    def test_malformed_canonical_config_fails_closed(self, app, bad_canonical):
        with app.app_context():
            app.config["L_GEDO_URL"] = bad_canonical
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.validate_installation_url(
                    "https://gedo.exemplo.licilink.com.br/callback", "gedo"
                )

    def test_malformed_canonical_config_never_falls_back_to_product_url(self, app):
        with app.app_context():
            app.config["L_GEDO_URL"] = None
            with pytest.raises(OrganizationProductInstallationError) as exc_info:
                OrganizationProductInstallationService.validate_installation_url(
                    "https://produto-issue-68.local/callback", "gedo"
                )
            # `Product.url` (o legado, "https://produto-issue-68.local")
            # nunca é usado como destino aceito por engano.
            assert "produto-issue-68.local" not in str(exc_info.value)


# Validador: ausência de vazamento -------------------------------------------

class TestValidateInstallationUrlMessagesNeverLeakRawValues:
    def test_message_never_contains_raw_url(self, app):
        with app.app_context():
            suspicious = "javascript:<<valor-bruto-suspeito-nao-deve-vazar>>"
            with pytest.raises(OrganizationProductInstallationError) as exc_info:
                OrganizationProductInstallationService.validate_installation_url(suspicious, "gedo")
            assert suspicious not in str(exc_info.value)

    def test_message_never_contains_raw_product_code(self, app):
        with app.app_context():
            suspicious_product = "<<produto-bruto-suspeito>>"
            with pytest.raises(OrganizationProductInstallationError) as exc_info:
                OrganizationProductInstallationService.validate_installation_url(
                    _valid_https_url(), suspicious_product
                )
            assert suspicious_product not in str(exc_info.value)


# Service administrativo: configure_installation -----------------------------

class TestConfigureInstallationService:
    def _build_org_product(self, product_code="gedo", org_product_status="active"):
        org = _create_organization()
        product = _create_product(code=product_code)
        org_product = _create_org_product(org, product, status=org_product_status)
        return org, product, org_product

    def test_creates_installation_when_none_exists(self, app):
        with app.app_context():
            org, product, org_product = self._build_org_product()
            url = _valid_https_url()

            installation = OrganizationProductInstallationService.configure_installation(
                org.id, product.code, url, actor_user_id=None,
            )

            assert installation.url == url
            assert installation.organization_product_id == org_product.id
            # Decisão de segurança (revisão técnica pós-implementação,
            # registrada na Issue #68): criação começa explicitamente
            # inativa, nunca ativa por padrão.
            assert installation.is_active is False
            assert OrganizationProductInstallation.query.filter_by(
                organization_product_id=org_product.id
            ).count() == 1

    def test_updates_url_of_existing_installation(self, app):
        with app.app_context():
            org, product, org_product = self._build_org_product()
            OrganizationProductInstallationService.configure_installation(
                org.id, product.code, _valid_https_url("primeira.local"), actor_user_id=None,
            )
            updated = OrganizationProductInstallationService.configure_installation(
                org.id, product.code, _valid_https_url("segunda.local"), actor_user_id=None,
            )

            assert updated.url == _valid_https_url("segunda.local")
            assert OrganizationProductInstallation.query.filter_by(
                organization_product_id=org_product.id
            ).count() == 1

    def test_resubmitting_same_url_is_rejected_without_audit(self, app):
        with app.app_context():
            org, product, org_product = self._build_org_product()
            url = _valid_https_url()
            OrganizationProductInstallationService.configure_installation(
                org.id, product.code, url, actor_user_id=None,
            )
            audit_before = AuditLog.query.count()

            with pytest.raises(OrganizationProductInstallationError) as exc_info:
                OrganizationProductInstallationService.configure_installation(
                    org.id, product.code, url, actor_user_id=None,
                )
            assert not isinstance(exc_info.value, OrganizationProductInstallationOperationError)
            assert AuditLog.query.count() == audit_before

    def test_allowed_even_with_inactive_subscription(self, app):
        with app.app_context():
            org, product, org_product = self._build_org_product(org_product_status="inactive")
            installation = OrganizationProductInstallationService.configure_installation(
                org.id, product.code, _valid_https_url(), actor_user_id=None,
            )
            assert installation is not None
            # Assinatura permanece exatamente como estava - configurar a
            # instalação nunca concede acesso.
            db.session.refresh(org_product)
            assert org_product.status == "inactive"

    def test_missing_organization_product_rejected(self, app):
        with app.app_context():
            org = _create_organization()
            product = _create_product(code="gedo")
            # Nenhum OrganizationProduct criado de propósito.
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.configure_installation(
                    org.id, product.code, _valid_https_url(), actor_user_id=None,
                )
            assert OrganizationProductInstallation.query.count() == 0

    def test_never_creates_organization_product_implicitly(self, app):
        with app.app_context():
            org = _create_organization()
            _create_product(code="gedo")
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.configure_installation(
                    org.id, "gedo", _valid_https_url(), actor_user_id=None,
                )
            assert OrganizationProduct.query.filter_by(organization_id=org.id).count() == 0

    def test_nonexistent_organization_rejected(self, app):
        with app.app_context():
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.configure_installation(
                    uuid.uuid4(), "gedo", _valid_https_url(), actor_user_id=None,
                )

    def test_nonexistent_or_noncanonical_product_code_rejected(self, app):
        with app.app_context():
            org = _create_organization()
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.configure_installation(
                    org.id, "produto-inexistente", _valid_https_url(), actor_user_id=None,
                )

    def test_product_code_must_match_a_persisted_product_case_sensitively(self, app):
        """Ajuste de precisão (revisão técnica, item B1): o nome/docstring
        anterior ("usa o product_code persistido, nunca o literal cru da
        rota") sugeria provar uma distinção que este teste não consegue
        demonstrar - `_resolve_organization_product` resolve `product`
        via `Product.query.filter_by(code=product_code)`, então sempre
        que a resolução tem sucesso, `product.code == product_code` é
        estruturalmente garantido (correspondência exata da própria
        consulta); não existe cenário em que os dois valores divirjam
        depois de uma resolução bem-sucedida. O que este teste
        efetivamente comprova é mais estreito: `product_code` precisa
        corresponder a um `Product.code` persistido de forma exata
        (case-sensitive) - "GEDO" (maiúsculo) não corresponde a
        "gedo" (persistido) e é rejeitado como produto inválido, nunca
        aceito por coincidência de string."""
        with app.app_context():
            org, product, org_product = self._build_org_product()
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.configure_installation(
                    org.id, "GEDO", _valid_https_url(), actor_user_id=None,
                )

    def test_concurrent_style_creation_never_duplicates_installation(self, app):
        """Simulação sequencial (mesmo padrão já aceito nas Issues
        #64-#67): a segunda tentativa de criação para o MESMO
        `OrganizationProduct` é tratada como atualização (upsert), nunca
        cria uma segunda linha."""
        with app.app_context():
            org, product, org_product = self._build_org_product()
            first = OrganizationProductInstallationService.configure_installation(
                org.id, product.code, _valid_https_url("um.local"), actor_user_id=None,
            )
            second = OrganizationProductInstallationService.configure_installation(
                org.id, product.code, _valid_https_url("dois.local"), actor_user_id=None,
            )
            assert first.id == second.id
            assert OrganizationProductInstallation.query.filter_by(
                organization_product_id=org_product.id
            ).count() == 1

    def test_integrity_error_during_creation_is_operational_and_rolls_back(self, app, monkeypatch):
        """Cobertura B1 da revisão técnica: o branch `except
        IntegrityError` já existia e foi verificado como funcionalmente
        correto por prova empírica direta durante a revisão, mas não
        tinha nenhum teste automatizado - `IntegrityError` era
        importado no arquivo e nunca usado. Força `db.session.flush`
        (o ponto em que o INSERT da nova instalação é enviado ao banco)
        a levantar um `IntegrityError` sintético, simulando uma corrida
        que escapou do lock (`with_for_update()` é no-op no SQLite dos
        testes)."""
        with app.app_context():
            org, product, org_product = self._build_org_product()

            original_flush = db.session.flush

            def _raise_integrity_error():
                raise IntegrityError("synthetic unique violation", {}, Exception("synthetic"))

            monkeypatch.setattr(db.session, "flush", _raise_integrity_error)

            with pytest.raises(OrganizationProductInstallationOperationError) as exc_info:
                OrganizationProductInstallationService.configure_installation(
                    org.id, product.code, _valid_https_url(), actor_user_id=None,
                )
            assert isinstance(exc_info.value.__cause__, IntegrityError)

            # Restaura ANTES de consultar o banco de novo - nunca deixa
            # o monkeypatch ativo além do necessário.
            monkeypatch.setattr(db.session, "flush", original_flush)

            assert OrganizationProductInstallation.query.count() == 0
            assert AuditLog.query.filter(
                AuditLog.action.like("organization_product_installation.%")
            ).count() == 0

            # A sessão continua utilizável após o rollback - uma
            # operação subsequente bem-sucedida não exige nenhuma
            # limpeza manual adicional (mesmo padrão já estabelecido
            # nas Issues #47/#66 para este tipo de teste).
            recovered = OrganizationProductInstallationService.configure_installation(
                org.id, product.code, _valid_https_url(), actor_user_id=None,
            )
            assert recovered is not None
            assert OrganizationProductInstallation.query.count() == 1

    def test_invalid_url_never_calls_the_database_write(self, app):
        with app.app_context():
            org, product, org_product = self._build_org_product()
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.configure_installation(
                    org.id, product.code, "javascript:alert(1)", actor_user_id=None,
                )
            assert OrganizationProductInstallation.query.count() == 0

    def test_exact_audit_event_on_creation(self, app):
        with app.app_context():
            org, product, org_product = self._build_org_product()
            actor = _create_user()

            installation = OrganizationProductInstallationService.configure_installation(
                org.id, product.code, _valid_https_url(), actor_user_id=actor.id,
            )

            logs = AuditLog.query.filter_by(
                action="organization_product_installation.created"
            ).all()
            assert len(logs) == 1
            log = logs[0]
            assert log.user_id == actor.id
            assert log.organization_id == org.id
            assert log.resource_type == "organization_product_installation"
            assert log.resource_id == installation.id
            assert log.details["product_code"] == product.code
            assert log.details["url"] == _valid_https_url()

    def test_exact_audit_event_on_url_update(self, app):
        with app.app_context():
            org, product, org_product = self._build_org_product()
            OrganizationProductInstallationService.configure_installation(
                org.id, product.code, _valid_https_url("primeira.local"), actor_user_id=None,
            )
            audit_before = AuditLog.query.count()

            installation = OrganizationProductInstallationService.configure_installation(
                org.id, product.code, _valid_https_url("segunda.local"), actor_user_id=None,
            )

            logs = AuditLog.query.filter_by(
                action="organization_product_installation.url_updated"
            ).all()
            assert len(logs) == 1
            assert AuditLog.query.count() == audit_before + 1

    def test_rollback_on_audit_failure_leaves_no_installation(self, app, monkeypatch):
        with app.app_context():
            org, product, org_product = self._build_org_product()
            before_count = OrganizationProductInstallation.query.count()

            def _raise(*a, **k):
                raise RuntimeError("falha sintetica de auditoria")
            monkeypatch.setattr(opis_module.AuditService, "log_action", staticmethod(_raise))

            with pytest.raises(OrganizationProductInstallationOperationError) as exc_info:
                OrganizationProductInstallationService.configure_installation(
                    org.id, product.code, _valid_https_url(), actor_user_id=None,
                )
            assert isinstance(exc_info.value.__cause__, RuntimeError)

        with app.app_context():
            assert OrganizationProductInstallation.query.count() == before_count

    def test_rollback_on_commit_failure_leaves_no_installation(self, app, monkeypatch):
        with app.app_context():
            org, product, org_product = self._build_org_product()
            before_count = OrganizationProductInstallation.query.count()

            def _raise():
                raise RuntimeError("falha sintetica de commit")
            monkeypatch.setattr(db.session, "commit", _raise)

            with pytest.raises(OrganizationProductInstallationOperationError):
                OrganizationProductInstallationService.configure_installation(
                    org.id, product.code, _valid_https_url(), actor_user_id=None,
                )

        with app.app_context():
            assert OrganizationProductInstallation.query.count() == before_count

    def test_update_preserves_id_public_id_created_at(self, app):
        with app.app_context():
            org, product, org_product = self._build_org_product()
            first = OrganizationProductInstallationService.configure_installation(
                org.id, product.code, _valid_https_url("primeira.local"), actor_user_id=None,
            )
            first_id = first.id
            first_public_id = first.public_id
            first_created_at = first.created_at

            second = OrganizationProductInstallationService.configure_installation(
                org.id, product.code, _valid_https_url("segunda.local"), actor_user_id=None,
            )

            assert second.id == first_id
            assert second.public_id == first_public_id
            assert second.created_at == first_created_at

    def test_update_preserves_credentials_and_pending_codes(self, app):
        with app.app_context():
            org, product, org_product = self._build_org_product()
            installation = OrganizationProductInstallationService.configure_installation(
                org.id, product.code, _valid_https_url("primeira.local"), actor_user_id=None,
            )
            installation_id = installation.id
            # Issue #68 (correção pós-revisão): a instalação nasce
            # inativa - precisa ser ativada explicitamente antes de
            # `issue_launch_code` aceitar emitir um código para ela.
            OrganizationProductInstallationService.activate_installation(
                org.id, product.code, actor_user_id=None,
            )
            secret = InstallationCredentialService.issue_credential(installation_id, actor_user_id=None)
            member = _create_user()
            OrganizationService.add_member(org.id, member.id, "member")
            issuance = ProductLaunchCodeService.issue_launch_code(member.id, org.id, product.code)

            OrganizationProductInstallationService.configure_installation(
                org.id, product.code, _valid_https_url("segunda.local"), actor_user_id=None,
            )

            assert OrganizationProductInstallationCredential.query.filter_by(
                organization_product_installation_id=installation_id
            ).count() == 1
            assert ProductLaunchCode.query.filter_by(
                organization_product_installation_id=installation_id
            ).count() == 1
            # A credencial emitida antes da atualização continua
            # autenticando normalmente.
            authenticated = InstallationCredentialService.authenticate_installation(
                OrganizationProductInstallation.query.get(installation_id).public_id, secret,
            )
            assert authenticated is not None


# Estado inicial inativo (decisão de segurança pós-revisão) -------------------

class TestConfigureInstallationInitialState:
    """Cobre especificamente a decisão de segurança adotada na Issue #68
    após a revisão técnica: uma instalação criada pelo service
    administrativo começa `is_active=False`, exigindo ativação
    explícita separada."""

    def test_creation_audits_only_created_never_deactivated(self, app):
        with app.app_context():
            org, product, org_product = self._build_org_product()
            OrganizationProductInstallationService.configure_installation(
                org.id, product.code, _valid_https_url(), actor_user_id=None,
            )

            assert AuditLog.query.filter_by(
                action="organization_product_installation.created"
            ).count() == 1
            # Nenhum evento artificial de desativação - o estado inicial
            # inativo é só um valor de campo dentro do próprio evento
            # `.created`, nunca uma transição de estado separada.
            assert AuditLog.query.filter_by(
                action="organization_product_installation.deactivated"
            ).count() == 0

    def test_update_preserves_inactive_state(self, app):
        with app.app_context():
            org, product, org_product = self._build_org_product()
            created = OrganizationProductInstallationService.configure_installation(
                org.id, product.code, _valid_https_url("primeira.local"), actor_user_id=None,
            )
            assert created.is_active is False

            updated = OrganizationProductInstallationService.configure_installation(
                org.id, product.code, _valid_https_url("segunda.local"), actor_user_id=None,
            )
            assert updated.is_active is False

    def test_update_preserves_active_state(self, app):
        with app.app_context():
            org, product, org_product = self._build_org_product()
            OrganizationProductInstallationService.configure_installation(
                org.id, product.code, _valid_https_url("primeira.local"), actor_user_id=None,
            )
            OrganizationProductInstallationService.activate_installation(
                org.id, product.code, actor_user_id=None,
            )

            updated = OrganizationProductInstallationService.configure_installation(
                org.id, product.code, _valid_https_url("segunda.local"), actor_user_id=None,
            )
            assert updated.is_active is True

    def _build_org_product(self, product_code="gedo", org_product_status="active"):
        org = _create_organization()
        product = _create_product(code=product_code)
        org_product = _create_org_product(org, product, status=org_product_status)
        return org, product, org_product


# Service administrativo: ativação/desativação --------------------------------

class TestActivateDeactivateInstallationService:
    def _build_installation(self, is_active=True):
        """Issue #68 (correção pós-revisão): `configure_installation`
        agora cria a instalação já INATIVA por padrão - portanto, para
        obter uma instalação ativa, é preciso ativá-la explicitamente
        aqui; nunca desativar uma que já nasce inativa (isso levantaria
        `OrganizationProductInstallationError`, "já está inativa")."""
        org = _create_organization()
        product = _create_product(code="gedo")
        org_product = _create_org_product(org, product)
        installation = OrganizationProductInstallationService.configure_installation(
            org.id, product.code, _valid_https_url(), actor_user_id=None,
        )
        if is_active:
            installation = OrganizationProductInstallationService.activate_installation(
                org.id, product.code, actor_user_id=None,
            )
        return org, product, installation

    def test_activate_inactive_installation_succeeds(self, app):
        with app.app_context():
            org, product, installation = self._build_installation(is_active=False)
            activated = OrganizationProductInstallationService.activate_installation(
                org.id, product.code, actor_user_id=None,
            )
            assert activated.is_active is True

    def test_activate_already_active_is_domain_error(self, app):
        with app.app_context():
            org, product, installation = self._build_installation(is_active=True)
            with pytest.raises(OrganizationProductInstallationError) as exc_info:
                OrganizationProductInstallationService.activate_installation(
                    org.id, product.code, actor_user_id=None,
                )
            assert not isinstance(exc_info.value, OrganizationProductInstallationOperationError)

    def test_deactivate_active_installation_succeeds(self, app):
        with app.app_context():
            org, product, installation = self._build_installation(is_active=True)
            deactivated = OrganizationProductInstallationService.deactivate_installation(
                org.id, product.code, actor_user_id=None,
            )
            assert deactivated.is_active is False

    def test_deactivate_already_inactive_is_domain_error(self, app):
        with app.app_context():
            org, product, installation = self._build_installation(is_active=False)
            with pytest.raises(OrganizationProductInstallationError) as exc_info:
                OrganizationProductInstallationService.deactivate_installation(
                    org.id, product.code, actor_user_id=None,
                )
            assert not isinstance(exc_info.value, OrganizationProductInstallationOperationError)

    def test_toggle_preserves_url_public_id_and_credentials(self, app):
        with app.app_context():
            org, product, installation = self._build_installation(is_active=True)
            original_url = installation.url
            original_public_id = installation.public_id
            secret = InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)

            OrganizationProductInstallationService.deactivate_installation(
                org.id, product.code, actor_user_id=None,
            )
            reactivated = OrganizationProductInstallationService.activate_installation(
                org.id, product.code, actor_user_id=None,
            )

            assert reactivated.url == original_url
            assert reactivated.public_id == original_public_id
            assert OrganizationProductInstallationCredential.query.filter_by(
                organization_product_installation_id=installation.id
            ).count() == 1

    def test_no_installation_configured_is_domain_error(self, app):
        with app.app_context():
            org = _create_organization()
            product = _create_product(code="gedo")
            _create_org_product(org, product)
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.activate_installation(
                    org.id, product.code, actor_user_id=None,
                )

    def test_exact_audit_event_on_activate_and_deactivate(self, app):
        with app.app_context():
            # `is_active=False`: nenhuma ativação de setup, para que o
            # único evento `.activated` produzido abaixo seja o do
            # próprio teste (a criação em si já não audita nenhum
            # evento de ativação/desativação - só `.created`).
            org, product, installation = self._build_installation(is_active=False)
            actor = _create_user()

            OrganizationProductInstallationService.activate_installation(
                org.id, product.code, actor_user_id=actor.id,
            )
            activated_logs = AuditLog.query.filter_by(
                action="organization_product_installation.activated"
            ).all()
            assert len(activated_logs) == 1
            assert activated_logs[0].user_id == actor.id
            assert activated_logs[0].resource_id == installation.id

            OrganizationProductInstallationService.deactivate_installation(
                org.id, product.code, actor_user_id=actor.id,
            )
            deactivated_logs = AuditLog.query.filter_by(
                action="organization_product_installation.deactivated"
            ).all()
            assert len(deactivated_logs) == 1
            assert deactivated_logs[0].user_id == actor.id
            assert deactivated_logs[0].resource_id == installation.id

    def test_no_audit_on_rejected_transition(self, app):
        with app.app_context():
            org, product, installation = self._build_installation(is_active=True)
            audit_before = AuditLog.query.count()
            with pytest.raises(OrganizationProductInstallationError):
                OrganizationProductInstallationService.activate_installation(
                    org.id, product.code, actor_user_id=None,
                )
            assert AuditLog.query.count() == audit_before


# Service administrativo: leitura ---------------------------------------------

class TestListInstallationsByOrganization:
    def test_empty_organization_returns_empty_dict(self, app):
        with app.app_context():
            org = _create_organization()
            result = OrganizationProductInstallationService.list_installations_by_organization(org.id)
            assert result == {}

    def test_returns_installations_keyed_by_product_id(self, app):
        with app.app_context():
            org = _create_organization()
            product = _create_product(code="gedo")
            _create_org_product(org, product)
            installation = OrganizationProductInstallationService.configure_installation(
                org.id, product.code, _valid_https_url(), actor_user_id=None,
            )

            result = OrganizationProductInstallationService.list_installations_by_organization(org.id)

            assert result == {product.id: installation}

    def test_product_without_installation_absent_from_dict(self, app):
        with app.app_context():
            org = _create_organization()
            product = _create_product(code="gedo")
            _create_org_product(org, product)
            # Nenhuma instalação configurada.
            result = OrganizationProductInstallationService.list_installations_by_organization(org.id)
            assert result == {}

    def test_never_creates_or_modifies_any_record(self, app):
        with app.app_context():
            org = _create_organization()
            product = _create_product(code="gedo")
            _create_org_product(org, product)
            before_installations = OrganizationProductInstallation.query.count()
            before_audit = AuditLog.query.count()

            OrganizationProductInstallationService.list_installations_by_organization(org.id)

            assert OrganizationProductInstallation.query.count() == before_installations
            assert AuditLog.query.count() == before_audit
