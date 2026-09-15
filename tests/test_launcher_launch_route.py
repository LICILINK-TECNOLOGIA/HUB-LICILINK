"""Issue #71: `POST /launch/<product_code>` (`dashboard.launch`) - a rota
que inicia o handoff federado do launcher, chamando
`ProductLaunchCodeService.issue_launch_code` exatamente uma vez com
argumentos de origem exclusivamente confiável (servidor) e transportando
o código emitido ao produto via a página intermediária de auto-submit
(`dashboard/launch_handoff.html`, coberta separadamente em
`tests/test_launcher_launch_handoff.py`).

Não duplica a suíte já existente de `ProductLaunchCodeService`
(`tests/test_product_launch_code_service.py`) - usa fixtures reais de
banco (nunca mocka a cadeia de revalidação inteira) e reserva spy/
monkeypatch só para provar: (a) argumentos exatos e número de chamadas
ao service; (b) fronteiras de erro operacional/inesperado que exigem
simular uma falha (banco, renderização) sem depender de um cenário real
de infraestrutura quebrada."""
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.models import (
    AuditLog,
    Organization,
    OrganizationMember,
    OrganizationProduct,
    OrganizationProductInstallation,
    Product,
    ProductLaunchCode,
    User,
)
from app.services.organization_service import OrganizationService
from app.services.product_launch_code_service import (
    ProductLaunchCodeOperationError,
    ProductLaunchCodeService,
)

SYNTHETIC_PASSWORD = "senha-sintetica-issue-71-rota"

_LAUNCH_ISSUED_ACTION = 'organization_product_installation.launch_code_issued'


def _create_user(email=None, is_active=True, verified=True, name="Usuario Issue 71"):
    user = User(
        name=name,
        email=email or f"usuario.issue71.{uuid.uuid4().hex[:8]}@example.test",
        email_verified_at=datetime.utcnow() if verified else None,
        is_active=is_active,
    )
    user.set_password(SYNTHETIC_PASSWORD)
    db.session.add(user)
    db.session.commit()
    return user


def _create_organization(is_active=True, legal_name=None):
    org = Organization(
        legal_name=legal_name or f"Organizacao Issue 71 {uuid.uuid4().hex[:8]}",
        is_active=is_active,
    )
    db.session.add(org)
    db.session.commit()
    return org


def _create_product(code="gedo", url="https://produto-issue-71.local"):
    product = Product(
        code=code, name="Produto Issue 71", description="Descricao Produto Issue 71", url=url,
    )
    db.session.add(product)
    db.session.commit()
    return product


def _create_org_product(org, product, status="active"):
    org_product = OrganizationProduct(organization_id=org.id, product_id=product.id, status=status)
    db.session.add(org_product)
    db.session.commit()
    return org_product


def _create_installation(org_product, url="https://instalacao-issue-71.local", is_active=True):
    installation = OrganizationProductInstallation(
        organization_product_id=org_product.id, url=url, is_active=is_active,
    )
    db.session.add(installation)
    db.session.commit()
    return installation


def _build_valid_chain(product_code="gedo"):
    """Cadeia completa e válida - usuário ativo/verificado, vínculo
    `active`, organização ativa, produto canônico com assinatura
    `active`, instalação ativa com URL válida (mesmo padrão de
    `tests/test_product_launch_code_service.py::_build_valid_chain`)."""
    user = _create_user()
    organization = _create_organization(is_active=True)
    OrganizationService.add_member(organization.id, user.id, "member")
    product = _create_product(code=product_code)
    org_product = _create_org_product(organization, product, status="active")
    installation = _create_installation(org_product, is_active=True)
    return {
        "user": user, "organization": organization, "product": product,
        "org_product": org_product, "installation": installation,
    }


def _login(client, get_csrf_token, email):
    return client.post("/login", data={
        "email": email,
        "password": SYNTHETIC_PASSWORD,
        "csrf_token": get_csrf_token(client),
    })


def _launch_code_count():
    return ProductLaunchCode.query.count()


def _issued_audit_count():
    return AuditLog.query.filter_by(action=_LAUNCH_ISSUED_ACTION).count()


class TestGetIsNotAllowed:
    def test_get_returns_405_and_never_calls_service(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_valid_chain()
            product_code = chain["product"].code
            email = chain["user"].email
            count_before = _launch_code_count()
            audit_before = _issued_audit_count()

        _login(client, get_csrf_token, email)
        response = client.get(f"/launch/{product_code}")

        assert response.status_code == 405

        with app.app_context():
            # Nenhuma linha nova - prova indireta, porém suficiente, de
            # que o service nunca foi chamado (405 do roteamento do
            # Flask/Werkzeug nunca chega a executar a view).
            assert _launch_code_count() == count_before
            assert _issued_audit_count() == audit_before


class TestAuthenticationAndCsrfAreEnforced:
    def test_post_without_authentication_redirects_to_login(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_valid_chain()
            product_code = chain["product"].code
            count_before = _launch_code_count()

        # Token CSRF válido obtido SEM autenticar (a geração de token do
        # Flask-WTF é por sessão, não por login) - isola a checagem de
        # autenticação da checagem de CSRF (testada separadamente
        # abaixo), nunca as duas mescladas no mesmo cenário.
        token = get_csrf_token(client, "/login")
        response = client.post(f"/launch/{product_code}", data={"csrf_token": token})

        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

        with app.app_context():
            assert _launch_code_count() == count_before

    def test_post_without_csrf_token_is_rejected(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_valid_chain()
            product_code = chain["product"].code
            email = chain["user"].email
            count_before = _launch_code_count()

        _login(client, get_csrf_token, email)
        response = client.post(f"/launch/{product_code}", data={})

        assert response.status_code == 400

        with app.app_context():
            assert _launch_code_count() == count_before

    def test_post_with_invalid_csrf_token_is_rejected(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_valid_chain()
            product_code = chain["product"].code
            email = chain["user"].email
            count_before = _launch_code_count()

        _login(client, get_csrf_token, email)
        response = client.post(f"/launch/{product_code}", data={"csrf_token": "token-invalido-issue-71"})

        assert response.status_code == 400

        with app.app_context():
            assert _launch_code_count() == count_before


class TestSuccessfulLaunchCallsServiceExactlyOnceWithTrustedArguments:
    def test_valid_post_renders_handoff_with_exactly_one_service_call(self, client, app, get_csrf_token, monkeypatch):
        with app.app_context():
            chain = _build_valid_chain()
            product_code = chain["product"].code
            email = chain["user"].email
            expected_user_id = chain["user"].id
            expected_org_id = chain["organization"].id
            count_before = _launch_code_count()
            audit_before = _issued_audit_count()

        calls = []
        original = ProductLaunchCodeService.issue_launch_code

        def _spy(user_id, organization_id, product_code_arg):
            calls.append((user_id, organization_id, product_code_arg))
            return original(user_id, organization_id, product_code_arg)

        monkeypatch.setattr(ProductLaunchCodeService, "issue_launch_code", staticmethod(_spy))

        _login(client, get_csrf_token, email)
        token = get_csrf_token(client, "/")
        response = client.post(f"/launch/{product_code}", data={"csrf_token": token})

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert 'name="code"' in html
        assert "Redirecionando" in html

        # Exatamente uma chamada, com os argumentos exatos e confiáveis -
        # usuário de `current_user`, organização do helper server-side,
        # `product_code` repassado sem reconstrução.
        assert len(calls) == 1
        called_user_id, called_org_id, called_product_code = calls[0]
        assert called_user_id == expected_user_id
        assert called_org_id == expected_org_id
        assert called_product_code == product_code

        with app.app_context():
            assert _launch_code_count() == count_before + 1
            assert _issued_audit_count() == audit_before + 1

    def test_response_carries_no_store_no_cache_no_referrer_headers(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_valid_chain()
            product_code = chain["product"].code
            email = chain["user"].email

        _login(client, get_csrf_token, email)
        token = get_csrf_token(client, "/")
        response = client.post(f"/launch/{product_code}", data={"csrf_token": token})

        assert response.status_code == 200
        assert response.headers.get("Cache-Control") == "no-store"
        assert response.headers.get("Pragma") == "no-cache"
        assert response.headers.get("Referrer-Policy") == "no-referrer"

    def test_no_location_header_and_no_code_in_url(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_valid_chain()
            product_code = chain["product"].code
            email = chain["user"].email

        _login(client, get_csrf_token, email)
        token = get_csrf_token(client, "/")
        response = client.post(f"/launch/{product_code}", data={"csrf_token": token})

        assert response.status_code == 200
        assert "Location" not in response.headers

    def test_two_legitimate_submissions_issue_two_distinct_codes(self, client, app, get_csrf_token):
        """Nenhuma promessa de idempotência: cada POST válido emite um
        novo código (comportamento já definido pela #65/#66)."""
        with app.app_context():
            chain = _build_valid_chain()
            product_code = chain["product"].code
            email = chain["user"].email
            count_before = _launch_code_count()

        _login(client, get_csrf_token, email)

        token_1 = get_csrf_token(client, "/")
        response_1 = client.post(f"/launch/{product_code}", data={"csrf_token": token_1})
        token_2 = get_csrf_token(client, "/")
        response_2 = client.post(f"/launch/{product_code}", data={"csrf_token": token_2})

        assert response_1.status_code == 200
        assert response_2.status_code == 200

        html_1 = response_1.data.decode("utf-8")
        html_2 = response_2.data.decode("utf-8")

        def _extract_code(html):
            match = re.search(r'name="code" value="([^"]+)"', html)
            assert match is not None
            return match.group(1)

        assert _extract_code(html_1) != _extract_code(html_2)

        with app.app_context():
            assert _launch_code_count() == count_before + 2


class TestNoSensitiveValueAcceptedFromClient:
    def test_organization_id_and_destination_url_in_form_body_are_ignored(self, client, app, get_csrf_token):
        """A rota não tem nenhum campo de `organization_id`/`destination_url`
        no seu contrato - mesmo que o cliente envie esses campos no corpo
        do POST (tentando apontar para uma organização alheia e um
        destino/código forjados), eles não têm nenhum efeito: a rota
        nunca os lê, e a emissão continua vinculada exclusivamente à
        organização resolvida pelo helper server-side."""
        with app.app_context():
            chain = _build_valid_chain()
            other_org = _create_organization(is_active=True, legal_name="Organizacao Alheia Issue 71")
            other_org_id = other_org.id
            product_code = chain["product"].code
            email = chain["user"].email
            expected_installation_id = chain["installation"].id

        _login(client, get_csrf_token, email)
        token = get_csrf_token(client, "/")
        response = client.post(f"/launch/{product_code}", data={
            "csrf_token": token,
            "organization_id": str(other_org_id),
            "destination_url": "https://destino-forjado-issue-71.invalid",
            "code": "codigo-forjado-issue-71",
        })

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "destino-forjado-issue-71.invalid" not in html
        assert "codigo-forjado-issue-71" not in html

        with app.app_context():
            last_code = ProductLaunchCode.query.order_by(ProductLaunchCode.id.desc()).first()
            assert last_code.organization_product_installation_id == expected_installation_id


class TestNoCurrentOrganization:
    def test_user_without_organization_gets_generic_error_without_calling_service(self, client, app, get_csrf_token, monkeypatch):
        with app.app_context():
            user = _create_user()
            email = user.email
            count_before = _launch_code_count()

        calls = []
        monkeypatch.setattr(
            ProductLaunchCodeService, "issue_launch_code",
            staticmethod(lambda *a, **k: calls.append((a, k))),
        )

        _login(client, get_csrf_token, email)
        token = get_csrf_token(client, "/")
        response = client.post("/launch/gedo", data={"csrf_token": token}, follow_redirects=True)

        assert response.status_code == 200
        assert calls == []

        with app.app_context():
            assert _launch_code_count() == count_before


class TestMultipleOrganizationsResolveDeterministically:
    def test_launch_uses_the_same_organization_the_get_would_resolve(self, client, app, get_csrf_token):
        with app.app_context():
            user = _create_user()
            email = user.email
            org_recent = _create_organization(legal_name="Organizacao Rota Recente Issue 71")
            org_earliest = _create_organization(legal_name="Organizacao Rota Antiga Issue 71")
            OrganizationService.add_member(org_recent.id, user.id, "member")
            OrganizationService.add_member(org_earliest.id, user.id, "member")

            base = datetime(2026, 1, 1, tzinfo=timezone.utc)
            OrganizationMember.query.filter_by(organization_id=org_recent.id, user_id=user.id).one().created_at = base + timedelta(days=1)
            OrganizationMember.query.filter_by(organization_id=org_earliest.id, user_id=user.id).one().created_at = base
            db.session.commit()

            product_recent = _create_product(code="hunt", url="https://produto-recente.local")
            product_earliest = _create_product(code="gedo", url="https://produto-antigo.local")
            org_product_recent = _create_org_product(org_recent, product_recent, status="active")
            org_product_earliest = _create_org_product(org_earliest, product_earliest, status="active")
            installation_earliest = _create_installation(org_product_earliest, is_active=True)
            _create_installation(org_product_recent, is_active=True)

            expected_installation_id = installation_earliest.id
            count_before = _launch_code_count()

        _login(client, get_csrf_token, email)
        token = get_csrf_token(client, "/")
        # Ambos os produtos são canônicos, mas somente o da organização
        # mais antiga (org_earliest) deve ser aceito - o helper resolve
        # sempre a mesma organização que o GET já listaria.
        response = client.post("/launch/gedo", data={"csrf_token": token})

        assert response.status_code == 200

        with app.app_context():
            assert _launch_code_count() == count_before + 1
            last_code = ProductLaunchCode.query.order_by(ProductLaunchCode.id.desc()).first()
            assert last_code.organization_product_installation_id == expected_installation_id


class TestMembershipOrOrganizationBecomingInactiveAfterGet:
    def test_membership_suspended_between_get_and_post_is_rejected(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_valid_chain()
            product_code = chain["product"].code
            email = chain["user"].email
            org_id = chain["organization"].id
            user_id = chain["user"].id
            count_before = _launch_code_count()
            audit_before = _issued_audit_count()

        _login(client, get_csrf_token, email)
        get_response = client.get("/")
        assert get_response.status_code == 200
        get_html = get_response.data.decode("utf-8")
        assert f'action="/launch/{product_code}"' in get_html
        assert "Acessar Sistema" in get_html

        with app.app_context():
            OrganizationService.suspend_member(org_id, user_id)

        token = get_csrf_token(client, "/")
        response = client.post(f"/launch/{product_code}", data={"csrf_token": token}, follow_redirects=True)

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        # Revisão técnica, achado B1 remanescente: mesma checagem de
        # segurança de mensagem já aplicada aos 7 cenários de
        # `TestDomainRejections` - a mensagem "Usuário não possui
        # vínculo ativo..." é estática hoje, mas esta asserção protege
        # contra regressão futura no service.
        _assert_flash_message_is_safe(html, product_code=product_code)

        with app.app_context():
            assert _launch_code_count() == count_before
            assert _issued_audit_count() == audit_before

    def test_organization_deactivated_between_get_and_post_is_rejected(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_valid_chain()
            product_code = chain["product"].code
            email = chain["user"].email
            org_id = chain["organization"].id
            count_before = _launch_code_count()
            audit_before = _issued_audit_count()

        _login(client, get_csrf_token, email)
        get_response = client.get("/")
        assert get_response.status_code == 200
        get_html = get_response.data.decode("utf-8")
        assert f'action="/launch/{product_code}"' in get_html
        assert "Acessar Sistema" in get_html

        with app.app_context():
            organization = Organization.query.get(org_id)
            organization.is_active = False
            db.session.commit()

        token = get_csrf_token(client, "/")
        response = client.post(f"/launch/{product_code}", data={"csrf_token": token}, follow_redirects=True)

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        # Revisão técnica, achado B1 remanescente: mesma checagem de
        # segurança de mensagem já aplicada aos 7 cenários de
        # `TestDomainRejections` - a mensagem "Organização inativa." é
        # estática hoje, mas esta asserção protege contra regressão
        # futura no service.
        _assert_flash_message_is_safe(html, product_code=product_code)

        with app.app_context():
            assert _launch_code_count() == count_before
            assert _issued_audit_count() == audit_before

    def test_user_deactivated_between_get_and_post_is_rejected(self, client, app, get_csrf_token):
        """Revisão técnica, achado B1 remanescente: `issue_launch_code`
        rejeita explicitamente `if not user.is_active: raise
        ProductLaunchCodeError("Usuário inativo.")`, mas nenhum teste
        cobria esse caminho. `user_loader` (`app/__init__.py::load_user`)
        só resolve `db.session.get(User, id)` a partir do `user_id` já
        assinado na sessão, e `login_required` do Flask-Login verifica
        apenas `current_user.is_authenticated` (`UserMixin.is_authenticated`
        é sempre `True`, nunca checa `is_active` - confirmado por leitura
        direta de `venv/Lib/site-packages/flask_login/{mixins,utils}.py`:
        `is_active` só é checado por `login_user()`, no momento do login,
        nunca por `user_loader`/`login_required` em requisições
        subsequentes) - uma sessão de login válida continua autenticada
        mesmo depois que o usuário é desativado, e a requisição chega de
        fato ao service, que é a única fronteira que efetivamente
        rejeita. Nenhuma camada de produção é forçada artificialmente
        aqui - este é o comportamento real do Flask-Login/`user_loader`
        já existentes.

        Nota sobre o teste em si (não sobre produção): desativar o
        usuário através de uma segunda transação (`with app.app_context():`
        aninhado, isolado da sessão que o cliente de teste reaproveita
        entre requisições) e então chamar `db.session.remove()` no
        contexto ambiente é necessário para que a requisição seguinte
        de fato releia o estado já commitado - sem isso, o
        SQLAlchemy da sessão reaproveitada pelo cliente de teste
        manteria em memória o objeto `User` já carregado por `_login`
        (antes da desativação), mascarando a mudança apenas neste
        arranjo de teste. Numa aplicação real, cada requisição HTTP
        recebe sua própria sessão nova por padrão - este passo não
        representa nenhum comportamento de produção, apenas neutraliza
        um artefato de reaproveitamento de contexto específico deste
        arranjo de teste."""
        with app.app_context():
            chain = _build_valid_chain()
            product_code = chain["product"].code
            email = chain["user"].email
            user_id = chain["user"].id
            count_before = _launch_code_count()
            audit_before = _issued_audit_count()

        _login(client, get_csrf_token, email)
        get_response = client.get("/")
        assert get_response.status_code == 200
        get_html = get_response.data.decode("utf-8")
        assert f'action="/launch/{product_code}"' in get_html
        assert "Acessar Sistema" in get_html

        with app.app_context():
            db.session.query(User).filter_by(id=user_id).update({"is_active": False})
            db.session.commit()
        db.session.remove()

        token = get_csrf_token(client, "/")
        response = client.post(f"/launch/{product_code}", data={"csrf_token": token}, follow_redirects=True)

        # A requisição chega à rota/service (nunca um redirect para
        # /login - a sessão continua autenticada) e é rejeitada como
        # `ProductLaunchCodeError` de domínio, nunca uma exceção
        # operacional mascarada.
        assert response.status_code == 200
        html = response.data.decode("utf-8")
        # Se a sessão tivesse sido invalidada (redirect para /login), a
        # asserção abaixo já falharia sozinha: a página de login não
        # renderiza nenhum "flash-msg flash-error" desta rota - por
        # isso `_assert_flash_message_is_safe` já é, por si só, prova
        # suficiente de que a requisição chegou ao launcher (não ao
        # login) e foi rejeitada como domínio, nunca como exceção
        # operacional mascarada (que nunca produziria este flash).
        assert "Redirecionando" not in html
        message = _assert_flash_message_is_safe(html, product_code=product_code)
        assert message == "Usuário inativo."

        with app.app_context():
            assert _launch_code_count() == count_before
            assert _issued_audit_count() == audit_before


_UUID_PATTERN_RE = re.compile(
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.IGNORECASE,
)
_FLASH_ERROR_RE = re.compile(r'<li class="flash-msg flash-error">([^<]*)</li>')

# Revisão técnica, achado B1 remanescente: qualquer sequência longa
# (>=32 caracteres) do alfabeto usado tanto pelo código de lançamento
# (`secrets.token_urlsafe(32)`, base64 URL-safe) quanto por um hash
# SHA-256 em hexadecimal (`code_hash`, 64 caracteres) ou um UUID sem
# hífens (32 caracteres hex) - nenhuma mensagem curada de
# `ProductLaunchCodeError` é longa o bastante para colidir com este
# padrão por acidente (são frases curtas em português, com espaços e
# pontuação), então qualquer ocorrência real é sinal de vazamento.
_HASH_OR_CODE_LIKE_RE = re.compile(r'[A-Za-z0-9_-]{32,}')

# Nomes de configuração sensíveis do próprio fluxo (`app/config.py`,
# `STRUCTURAL_PRODUCTS`) - nenhuma mensagem pública de
# `ProductLaunchCodeError` precisa citar o NOME da variável de ambiente
# por trás de uma URL canônica ou do TTL para ser compreensível ao
# usuário.
_SENSITIVE_CONFIG_NAMES = (
    "L_GEDO_URL", "L_KALENDER_URL", "L_HUNT_URL", "PRODUCT_LAUNCH_CODE_TTL_SECONDS",
)


def _assert_flash_message_is_safe(html, *, product_code=None):
    """Revisão técnica, achado B1 remanescente: helper único e
    reutilizável (extraído de `TestDomainRejections._assert_rejected_as_domain_error`)
    para a checagem de segurança da mensagem pública exibida em
    `flash(str(e), 'error')` por `dashboard.launch` - usado por todo
    cenário de rejeição de domínio real (`ProductLaunchCodeError`),
    dentro ou fora de `TestDomainRejections`. Opera exclusivamente sobre
    o HTML de resposta REAL (nunca sobre o código-fonte/AST do service)
    - a garantia é sobre o que o navegador efetivamente recebe.

    Confirma exatamente uma mensagem de domínio, e que ela nunca contém:
    UUID (`organization_id`/`user_id`); qualquer URL (`http://`/`https://`,
    o que cobre `destination_url`/`Product.url` por construção, já que
    ambos são sempre URLs); o `product_code` bruto usado na requisição,
    quando fornecido (entrada não confiável); os nomes das variáveis de
    configuração sensíveis do fluxo (`L_GEDO_URL`/`L_KALENDER_URL`/
    `L_HUNT_URL`/`PRODUCT_LAUNCH_CODE_TTL_SECONDS`); um padrão de
    código/hash bruto (>=32 caracteres do alfabeto usado por
    `secrets.token_urlsafe`/SHA-256 hex/UUID sem hífens); e a palavra
    `Traceback`. Nunca proíbe palavras públicas legítimas como "GEDO" ou
    "produto" - o alvo é interpolação de entrada bruta/dado interno,
    não vocabulário de negócio."""
    matches = _FLASH_ERROR_RE.findall(html)
    assert len(matches) == 1, f"esperada exatamente uma mensagem de erro (flash) no HTML, encontradas {len(matches)}"
    message = matches[0]

    assert _UUID_PATTERN_RE.search(message) is None, (
        f"mensagem de erro contém um UUID (possível vazamento de organization_id/user_id): {message!r}"
    )
    assert "http://" not in message and "https://" not in message, (
        f"mensagem de erro contém uma URL (possível vazamento de destination_url/Product.url): {message!r}"
    )
    if product_code is not None:
        assert product_code not in message, (
            f"mensagem de erro contém o product_code bruto {product_code!r}: {message!r}"
        )
    for config_name in _SENSITIVE_CONFIG_NAMES:
        assert config_name not in message, (
            f"mensagem de erro contém o nome de uma variável de configuração sensível {config_name!r}: {message!r}"
        )
    assert _HASH_OR_CODE_LIKE_RE.search(message) is None, (
        f"mensagem de erro contém um padrão de código/hash bruto: {message!r}"
    )
    assert "Traceback" not in message, f"mensagem de erro contém um traceback: {message!r}"

    return message


class TestDomainRejections:
    def _assert_rejected_as_domain_error(self, client, app, get_csrf_token, email, product_code, count_before):
        _login(client, get_csrf_token, email)
        token = get_csrf_token(client, "/")
        response = client.post(f"/launch/{product_code}", data={"csrf_token": token}, follow_redirects=True)

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        # Nunca uma página de erro dedicada, nunca 500 - sempre o mesmo
        # padrão flash + redirect ao launcher.
        assert "Redirecionando" not in html

        # Revisão técnica, achado B1: `dashboard.launch` confia em
        # `flash(str(e), 'error')` para toda `ProductLaunchCodeError` -
        # seguro hoje porque cada `raise` em
        # `product_launch_code_service.py` usa uma string estática,
        # nunca interpolando organization_id/product_code/URL/exceção
        # original. Esta asserção roda em TODOS os cenários de rejeição
        # de domínio abaixo (não um teste isolado e pontual) - detecta
        # uma regressão futura no service assim que qualquer um deles
        # passar a interpolar um valor sensível na mensagem.
        _assert_flash_message_is_safe(html, product_code=product_code)

        with app.app_context():
            assert _launch_code_count() == count_before

    def test_invalid_product_code_is_rejected(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_valid_chain()
            email = chain["user"].email
            count_before = _launch_code_count()

        self._assert_rejected_as_domain_error(
            client, app, get_csrf_token, email, "produto-nao-canonico-issue-71", count_before,
        )

    def test_missing_subscription_is_rejected(self, client, app, get_csrf_token):
        with app.app_context():
            user = _create_user()
            organization = _create_organization(is_active=True)
            OrganizationService.add_member(organization.id, user.id, "member")
            _create_product(code="gedo")
            # Nenhum OrganizationProduct criado - "unsubscribed".
            email = user.email
            count_before = _launch_code_count()

        self._assert_rejected_as_domain_error(client, app, get_csrf_token, email, "gedo", count_before)

    def test_inactive_subscription_is_rejected(self, client, app, get_csrf_token):
        with app.app_context():
            user = _create_user()
            organization = _create_organization(is_active=True)
            OrganizationService.add_member(organization.id, user.id, "member")
            product = _create_product(code="gedo")
            _create_org_product(organization, product, status="inactive")
            email = user.email
            count_before = _launch_code_count()

        self._assert_rejected_as_domain_error(client, app, get_csrf_token, email, "gedo", count_before)

    def test_missing_installation_is_rejected(self, client, app, get_csrf_token):
        with app.app_context():
            user = _create_user()
            organization = _create_organization(is_active=True)
            OrganizationService.add_member(organization.id, user.id, "member")
            product = _create_product(code="gedo")
            _create_org_product(organization, product, status="active")
            # Nenhuma OrganizationProductInstallation criada.
            email = user.email
            count_before = _launch_code_count()

        self._assert_rejected_as_domain_error(client, app, get_csrf_token, email, "gedo", count_before)

    def test_inactive_installation_is_rejected(self, client, app, get_csrf_token):
        with app.app_context():
            user = _create_user()
            organization = _create_organization(is_active=True)
            OrganizationService.add_member(organization.id, user.id, "member")
            product = _create_product(code="gedo")
            org_product = _create_org_product(organization, product, status="active")
            _create_installation(org_product, is_active=False)
            email = user.email
            count_before = _launch_code_count()

        self._assert_rejected_as_domain_error(client, app, get_csrf_token, email, "gedo", count_before)

    def test_active_installation_with_directly_persisted_invalid_url_is_rejected(self, client, app, get_csrf_token):
        """Instalação ativa cuja URL foi persistida diretamente (fixture,
        bypassando `configure_installation`/`validate_installation_url`)
        e é inválida pela política atual (aqui: contém query string,
        rejeitada por `validate_installation_url`) - o botão poderia ter
        aparecido como lançável no GET (`AccessService`/
        `list_installations_by_organization` não revalidam a URL), mas o
        POST falha de forma segura, sem fallback para `Product.url`."""
        with app.app_context():
            user = _create_user()
            organization = _create_organization(is_active=True)
            OrganizationService.add_member(organization.id, user.id, "member")
            product = _create_product(code="gedo", url="https://url-legada-product-issue-71.local")
            org_product = _create_org_product(organization, product, status="active")
            _create_installation(org_product, url="https://instalacao-invalida.local/?x=1", is_active=True)
            email = user.email
            count_before = _launch_code_count()

        self._assert_rejected_as_domain_error(client, app, get_csrf_token, email, "gedo", count_before)

        with app.app_context():
            # Confirma que a rejeição não caiu, silenciosamente, para a
            # URL legada global do produto.
            assert _launch_code_count() == count_before

    def test_invalid_ttl_configuration_is_rejected(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_valid_chain()
            product_code = chain["product"].code
            email = chain["user"].email
            count_before = _launch_code_count()

        app.config['PRODUCT_LAUNCH_CODE_TTL_SECONDS'] = -5
        try:
            self._assert_rejected_as_domain_error(
                client, app, get_csrf_token, email, product_code, count_before,
            )
        finally:
            app.config['PRODUCT_LAUNCH_CODE_TTL_SECONDS'] = 60


class TestOperationalAndUnexpectedErrorsPropagateWithoutBeingCaught:
    """Revisão técnica (achado A2): a rota captura exclusivamente
    `ProductLaunchCodeError`. `ProductLaunchCodeOperationError`, falhas
    de renderização e qualquer outra exceção não classificada não são
    tratadas por ela - propagam para o mecanismo padrão de exceção não
    tratada do Flask. Com `TESTING=True` (`TestingConfig`), o Flask
    define `propagate_exceptions=True` por padrão - uma exceção não
    tratada por nenhuma view/error handler é relançada de verdade na
    chamada de `client.post(...)`, nunca convertida silenciosamente
    numa resposta HTTP - por isso estes testes usam `pytest.raises`, não
    `response.status_code`."""

    def test_operation_error_propagates_and_is_not_converted_to_a_flash_redirect(self, client, app, get_csrf_token, monkeypatch):
        with app.app_context():
            chain = _build_valid_chain()
            product_code = chain["product"].code
            email = chain["user"].email
            count_before = _launch_code_count()

        # Login e obtenção do token ANTES do monkeypatch - o próprio
        # login já executa um `db.session.commit()` (auditoria de
        # `user_login`), que não pode ser afetado pela falha sintética
        # (isolada exclusivamente ao commit da emissão, dentro do POST).
        _login(client, get_csrf_token, email)
        token = get_csrf_token(client, "/")

        def _raise(*args, **kwargs):
            raise RuntimeError("falha sintetica de banco (Issue #71)")

        monkeypatch.setattr(db.session, "commit", _raise)

        # A falha ocorre dentro de `issue_launch_code` (commit da
        # emissão) - o próprio service já a converte em
        # `ProductLaunchCodeOperationError` antes de relançar; a rota
        # não a captura, então ela propaga até aqui sem nunca virar uma
        # resposta HTTP 302/200.
        with pytest.raises(ProductLaunchCodeOperationError):
            client.post(f"/launch/{product_code}", data={"csrf_token": token})

        with app.app_context():
            assert _launch_code_count() == count_before

    def test_render_failure_after_successful_issuance_propagates_without_masking_the_prior_commit(self, client, app, get_csrf_token, monkeypatch):
        """Fronteira transacional (corpo da #71): `issue_launch_code` já
        commitou a emissão antes de a rota tentar renderizar o handoff.
        Uma falha de renderização depois disso propaga sem ser
        capturada pela rota - não deve: reverter o commit já concluído,
        criar um segundo código, criar auditoria adicional, ou ser
        mascarada como se a emissão tivesse falhado."""
        with app.app_context():
            chain = _build_valid_chain()
            product_code = chain["product"].code
            email = chain["user"].email
            count_before = _launch_code_count()
            audit_before = _issued_audit_count()

        import app.blueprints.dashboard as dashboard_module
        original_render_template = dashboard_module.render_template

        def _boom(template_name_or_list, *args, **kwargs):
            if template_name_or_list == 'dashboard/launch_handoff.html':
                raise RuntimeError("falha sintetica de renderizacao (Issue #71)")
            return original_render_template(template_name_or_list, *args, **kwargs)

        monkeypatch.setattr(dashboard_module, "render_template", _boom)

        _login(client, get_csrf_token, email)
        token = get_csrf_token(client, "/")

        # A renderização ocorre FORA do `try` da rota (só a chamada a
        # `issue_launch_code` é protegida) - a falha sintética propaga
        # sem nenhuma conversão em flash/redirect.
        with pytest.raises(RuntimeError, match="falha sintetica de renderizacao"):
            client.post(f"/launch/{product_code}", data={"csrf_token": token})

        with app.app_context():
            # A emissão já havia sido commitada antes da falha de
            # renderização - permanece registrada (código não entregue,
            # expira pelo TTL, nunca reemitido/revertido por esta rota).
            assert _launch_code_count() == count_before + 1
            assert _issued_audit_count() == audit_before + 1

    def test_render_failure_does_not_trigger_a_second_service_call(self, client, app, get_csrf_token, monkeypatch):
        with app.app_context():
            chain = _build_valid_chain()
            product_code = chain["product"].code
            email = chain["user"].email

        import app.blueprints.dashboard as dashboard_module
        original_render_template = dashboard_module.render_template

        def _boom(template_name_or_list, *args, **kwargs):
            if template_name_or_list == 'dashboard/launch_handoff.html':
                raise RuntimeError("falha sintetica de renderizacao (Issue #71)")
            return original_render_template(template_name_or_list, *args, **kwargs)

        monkeypatch.setattr(dashboard_module, "render_template", _boom)

        calls = []
        original_issue = ProductLaunchCodeService.issue_launch_code

        def _spy(*args, **kwargs):
            calls.append((args, kwargs))
            return original_issue(*args, **kwargs)

        monkeypatch.setattr(ProductLaunchCodeService, "issue_launch_code", staticmethod(_spy))

        _login(client, get_csrf_token, email)
        token = get_csrf_token(client, "/")

        with pytest.raises(RuntimeError):
            client.post(f"/launch/{product_code}", data={"csrf_token": token})

        assert len(calls) == 1


class TestNoRawCodeOutsideTheHandoffField:
    def test_code_never_appears_in_flash_or_error_html(self, client, app, get_csrf_token, monkeypatch):
        with app.app_context():
            chain = _build_valid_chain()
            email = chain["user"].email

        _login(client, get_csrf_token, email)

        # Cenário de erro: assinatura ausente para outro produto
        # canônico - nenhum código chega a ser gerado, mas a checagem
        # abaixo comprova a AUSÊNCIA de qualquer valor de código na
        # resposta de erro (não há código emitido para vazar).
        with app.app_context():
            other_user = _create_user()
            other_org = _create_organization(is_active=True)
            OrganizationService.add_member(other_org.id, other_user.id, "member")
            _create_product(code="hunt")
            other_email = other_user.email

        _login(client, get_csrf_token, other_email)
        token = get_csrf_token(client, "/")
        response = client.post("/launch/hunt", data={"csrf_token": token}, follow_redirects=True)
        html = response.data.decode("utf-8")
        assert 'name="code"' not in html

    def test_no_audit_log_contains_the_raw_code_or_hash_as_plaintext_marker(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_valid_chain()
            product_code = chain["product"].code
            email = chain["user"].email

        _login(client, get_csrf_token, email)
        token = get_csrf_token(client, "/")
        response = client.post(f"/launch/{product_code}", data={"csrf_token": token})
        html = response.data.decode("utf-8")

        match = re.search(r'name="code" value="([^"]+)"', html)
        assert match is not None
        raw_code = match.group(1)

        with app.app_context():
            entries = AuditLog.query.filter_by(action=_LAUNCH_ISSUED_ACTION).all()
            assert len(entries) >= 1
            for entry in entries:
                assert raw_code not in str(entry.details)
