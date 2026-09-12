"""Issue #67: contrato HTTP de `POST /api/federation/launch/consume`
(`app/blueprints/federation.py`). Cobre exclusivamente a camada de rota -
autenticação Basic, corpo/limites HTTP, códigos de status e cabeçalhos de
resposta, isenção de CSRF (e sua não-propagação a outras rotas), e
ausência de vazamento de segredo/código/hash na resposta. O núcleo
transacional do consumo (`ProductLaunchCodeService.consume_launch_code`)
já é coberto por `tests/test_product_launch_code_service.py`; aqui ele é
exercitado apenas como dependência real, nunca reimplementado nem
mockado, exceto onde o próprio teste exige simular uma falha
operacional.

`_build_chain_with_credential_and_code` devolve SOMENTE valores
primitivos (str/UUID), nunca os objetos ORM em si - evita o padrão de
erro (instância detached/expirada) já identificado em
`tests/test_product_launch_code_service.py`: qualquer atributo de um
objeto ORM só é seguro de ler DENTRO do mesmo `with app.app_context():`
em que o objeto foi criado/carregado."""
import base64
import json
import uuid
from datetime import datetime

import pytest
from werkzeug.test import EnvironBuilder

from app.extensions import db
from app.models import (
    AuditLog,
    Organization,
    OrganizationProduct,
    OrganizationProductInstallation,
    Product,
    ProductLaunchCode,
    User,
)
from app.services.installation_credential_service import InstallationCredentialService
from app.services.organization_service import OrganizationService
from app.services.product_launch_code_service import ProductLaunchCodeService

SYNTHETIC_PASSWORD = "senha-sintetica-issue-67-123"
ROUTE = "/api/federation/launch/consume"

_EXPECTED_FIELDS = {
    "contract_version", "issuer", "authorization_id", "sub", "email",
    "name", "organization_id", "product_code", "installation_public_id",
    "role",
}


def _create_user(email=None, is_active=True, verified=True):
    user = User(
        name="Usuario Issue 67",
        email=email or f"usuario.issue67.{uuid.uuid4().hex[:8]}@example.test",
        email_verified_at=datetime.utcnow() if verified else None,
        is_active=is_active,
    )
    user.set_password(SYNTHETIC_PASSWORD)
    db.session.add(user)
    db.session.commit()
    return user


def _create_organization(is_active=True):
    org = Organization(
        legal_name=f"Organizacao Issue 67 {uuid.uuid4().hex[:8]}",
        is_active=is_active,
    )
    db.session.add(org)
    db.session.commit()
    return org


def _create_product(code="gedo"):
    product = Product(
        code=code,
        name="Produto Issue 67",
        description="Descricao Produto Issue 67",
        url="https://produto-issue-67.local",
    )
    db.session.add(product)
    db.session.commit()
    return product


def _create_org_product(org, product, status="active"):
    org_product = OrganizationProduct(organization_id=org.id, product_id=product.id, status=status)
    db.session.add(org_product)
    db.session.commit()
    return org_product


def _create_installation(org_product, is_active=True):
    installation = OrganizationProductInstallation(
        organization_product_id=org_product.id,
        url="https://instalacao-issue-67.local",
        is_active=is_active,
    )
    db.session.add(installation)
    db.session.commit()
    return installation


def _build_chain_with_credential_and_code(product_code="gedo", role="member"):
    """Monta a cadeia completa (usuário, organização, vínculo, produto,
    assinatura, instalação), emite uma credencial de instalação (#64) e um
    código de lançamento (#66) - ponto de partida de todo teste que exige
    uma requisição HTTP genuinamente autenticável e consumível. Deve ser
    chamada dentro de um `with app.app_context():`; o dicionário devolvido
    contém somente primitivos, seguros de usar fora desse bloco."""
    user = _create_user()
    organization = _create_organization(is_active=True)
    OrganizationService.add_member(organization.id, user.id, role)
    product = _create_product(code=product_code)
    org_product = _create_org_product(organization, product)
    installation = _create_installation(org_product)
    secret = InstallationCredentialService.issue_credential(installation.id, actor_user_id=None)
    issuance = ProductLaunchCodeService.issue_launch_code(user.id, organization.id, product_code)
    return {
        "user_id": str(user.id),
        "user_email": user.email,
        "user_name": user.name,
        "organization_id": str(organization.id),
        "product_code": product_code,
        "installation_id": installation.id,
        "installation_public_id": str(installation.public_id),
        "role": role,
        "secret": secret,
        "code": issuance.code,
    }


def _basic_auth_header(username, password):
    raw = f"{username}:{password}".encode("utf-8")
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


def _post(client, chain=None, *, public_id=None, secret=None, code=None, headers=None, **kwargs):
    if chain is not None:
        public_id = public_id if public_id is not None else chain["installation_public_id"]
        secret = secret if secret is not None else chain["secret"]
        code = code if code is not None else chain["code"]

    call_kwargs = {}
    if "json" not in kwargs and "data" not in kwargs:
        call_kwargs["json"] = {"code": code}
    call_kwargs.update(kwargs)

    final_headers = {}
    if public_id is not None or secret is not None:
        final_headers.update(_basic_auth_header(public_id, secret))
    if headers:
        final_headers.update(headers)

    return client.post(ROUTE, headers=final_headers, **call_kwargs)


def _post_without_content_length(app, body_bytes, headers=None):
    """Achado A2-1 da revisão técnica: simula uma requisição cujo corpo
    NÃO declara `Content-Length` (ex.: `Transfer-Encoding: chunked`) e
    cujo servidor WSGI sinaliza que sabe terminar o stream sozinho
    (`environ['wsgi.input_terminated'] = True`, mesma condição sob a
    qual o Werkzeug de fato aplica `request.max_content_length` a um
    stream sem `Content-Length` - ver `werkzeug.wsgi.get_input_stream`).
    O cliente de teste padrão do Werkzeug (`app.test_client()`) sempre
    calcula e define `Content-Length` sozinho para `data=`/`json=`, por
    isso este helper monta o ambiente WSGI manualmente via
    `EnvironBuilder`, removendo o header - a única forma de reproduzir
    de verdade o cenário que o achado descreve, em vez de continuar
    dependendo do header declarado pelo cliente."""
    builder = EnvironBuilder(
        path=ROUTE, method="POST", data=body_bytes,
        content_type="application/json", headers=headers or {},
    )
    environ = builder.get_environ()
    environ.pop("CONTENT_LENGTH", None)
    environ["wsgi.input_terminated"] = True
    with app.request_context(environ):
        return app.full_dispatch_request()


# Sucesso -----------------------------------------------------------------

class TestConsumeLaunchCodeSuccessContract:
    def test_valid_request_returns_200_with_exact_contract_fields(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        response = _post(client, chain)

        assert response.status_code == 200
        assert response.is_json
        body = response.get_json()
        assert set(body.keys()) == _EXPECTED_FIELDS

    def test_response_field_values_match_the_authenticated_installation_and_code(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        response = _post(client, chain)
        body = response.get_json()

        assert response.status_code == 200
        assert body["contract_version"] == 1
        assert body["issuer"] == "hub.licilink"
        assert body["sub"] == chain["user_id"]
        assert body["email"] == chain["user_email"]
        assert body["name"] == chain["user_name"]
        assert body["organization_id"] == chain["organization_id"]
        assert body["product_code"] == "gedo"
        assert body["installation_public_id"] == chain["installation_public_id"]
        assert body["role"] == "member"

    def test_success_response_has_no_store_cache_headers(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        response = _post(client, chain)

        assert response.status_code == 200
        assert response.headers.get("Cache-Control") == "no-store"
        assert response.headers.get("Pragma") == "no-cache"

    def test_second_attempt_with_same_code_is_rejected(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        first = _post(client, chain)
        second = _post(client, chain)

        assert first.status_code == 200
        assert second.status_code == 401
        assert second.get_json() == {"error": "invalid_credentials_or_code"}

    def test_exactly_one_audit_event_on_success(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        _post(client, chain)

        with app.app_context():
            events = AuditLog.query.filter_by(
                action="organization_product_installation.launch_code_consumed",
                resource_id=chain["installation_id"],
            ).all()
            assert len(events) == 1


# Corpo/estrutura HTTP (400) ------------------------------------------------

class TestConsumeLaunchCodeBodyValidation:
    def test_missing_content_type_returns_400(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        response = _post(
            client, chain,
            data='{"code": "qualquer"}',
        )
        assert response.status_code == 400
        assert response.get_json() == {"error": "invalid_request"}

    def test_wrong_content_type_returns_400(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        response = _post(
            client, chain,
            data="code=qualquer",
            content_type="application/x-www-form-urlencoded",
        )
        assert response.status_code == 400
        assert response.get_json() == {"error": "invalid_request"}

    def test_malformed_json_returns_400(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        response = _post(
            client, chain,
            data="{invalido",
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.get_json() == {"error": "invalid_request"}

    @pytest.mark.parametrize(
        "raw_body",
        ["[1, 2, 3]", '"apenas uma string"', "123", "true", "null"],
        ids=["array", "string", "numero", "booleano", "nulo"],
    )
    def test_non_object_json_top_level_returns_400(self, app, client, raw_body):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        response = _post(client, chain, data=raw_body, content_type="application/json")
        assert response.status_code == 400
        assert response.get_json() == {"error": "invalid_request"}

    @pytest.mark.parametrize(
        "invalid_code",
        [None, "", 12345, [], {}, True, "x" * 257],
        ids=["nulo", "vazio", "numero", "lista", "dict", "booleano", "excede_256"],
    )
    def test_invalid_code_field_returns_400(self, app, client, invalid_code):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        response = _post(client, chain, json={"code": invalid_code})
        assert response.status_code == 400
        assert response.get_json() == {"error": "invalid_request"}

    def test_missing_code_field_returns_400(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        response = _post(client, chain, json={})
        assert response.status_code == 400
        assert response.get_json() == {"error": "invalid_request"}

    def test_body_over_4kib_returns_400(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        oversized_code = "x" * 5000
        response = _post(client, chain, json={"code": oversized_code})
        assert response.status_code == 400
        assert response.get_json() == {"error": "invalid_request"}

    def test_invalid_body_does_not_burn_a_real_code(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        _post(client, chain, json={"code": 12345})

        with app.app_context():
            launch_code = ProductLaunchCode.query.filter_by(
                organization_product_installation_id=chain["installation_id"]
            ).first()
            assert launch_code.consumed_at is None

    def test_400_response_has_no_store_cache_headers(self, app, client):
        response = client.post(ROUTE, json={"code": 123})
        assert response.status_code == 400
        assert response.headers.get("Cache-Control") == "no-store"
        assert response.headers.get("Pragma") == "no-cache"


# Limite real de corpo (Achado A2-1) ------------------------------------------

class TestConsumeLaunchCodeRealBodyLimit:
    """Achado A2-1 da revisão técnica: a checagem antiga só inspecionava
    `request.content_length` (o header `Content-Length` DECLARADO pelo
    cliente) - um cliente que o omite (ex.: `Transfer-Encoding:
    chunked`) contornava a rejeição inteiramente, e o corpo era lido
    sem nenhum teto real. A correção usa `request.max_content_length`
    (Flask/Werkzeug >= 3.1, settável por instância), que limita os
    bytes efetivamente lidos do stream WSGI, independente do que o
    cliente declara. Estes testes provam o mecanismo real, nunca só o
    atalho de `Content-Length` honesto (já coberto por
    `TestConsumeLaunchCodeBodyValidation.test_body_over_4kib_returns_400`)."""

    def test_body_exactly_at_the_permitted_limit_is_not_rejected_by_size(self, app, client):
        # Corpo com o `code` REAL e válido, mas preenchido com um campo
        # extra ignorado (`padding`) até somar exatamente 4096 bytes -
        # isola o teste do teto de 256 caracteres do próprio campo
        # `code` (validação diferente, já coberta em
        # `TestConsumeLaunchCodeBodyValidation`). Provar sucesso (200)
        # exatamente no limite confirma que o tamanho do corpo, por si
        # só, nunca é motivo de rejeição até (e incluindo) 4096 bytes.
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        payload = {"code": chain["code"], "padding": ""}
        base_length = len(json.dumps(payload).encode("utf-8"))
        payload["padding"] = "a" * (4096 - base_length)
        body = json.dumps(payload).encode("utf-8")
        assert len(body) == 4096

        response = client.post(
            ROUTE, data=body, content_type="application/json",
            headers=_basic_auth_header(chain["installation_public_id"], chain["secret"]),
        )

        assert response.status_code == 200
        assert set(response.get_json().keys()) == _EXPECTED_FIELDS

    def test_body_over_limit_with_honest_content_length_never_calls_service(self, app, client, monkeypatch):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        calls = []
        monkeypatch.setattr(
            ProductLaunchCodeService, "consume_launch_code",
            staticmethod(lambda *a, **k: calls.append((a, k))),
        )

        response = _post(client, chain, json={"code": "x" * 5000})

        assert response.status_code == 400
        assert response.get_json() == {"error": "invalid_request"}
        assert response.content_type == "application/json"
        assert response.headers.get("Cache-Control") == "no-store"
        assert response.headers.get("Pragma") == "no-cache"
        assert calls == []

    def test_body_over_limit_without_content_length_is_rejected_by_real_stream_cap(self, app, client):
        # Reprodução real do achado: corpo maior que 4 KiB, mas SEM o
        # header `Content-Length` (simulando `Transfer-Encoding:
        # chunked` com um servidor WSGI que sinaliza
        # `wsgi.input_terminated`) - a checagem antiga (baseada só no
        # header declarado) deixaria isso passar inteiramente; o teto
        # real de `request.max_content_length` deve rejeitar mesmo
        # assim, via `RequestEntityTooLarge` capturado na rota.
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        oversized_body = json.dumps({"code": "y" * 5000}).encode("utf-8")
        assert len(oversized_body) > 4096

        response = _post_without_content_length(
            app, oversized_body,
            headers=_basic_auth_header(chain["installation_public_id"], chain["secret"]),
        )

        assert response.status_code == 400
        assert response.get_json() == {"error": "invalid_request"}
        assert response.content_type == "application/json"
        assert response.headers.get("Cache-Control") == "no-store"
        assert response.headers.get("Pragma") == "no-cache"

    def test_body_over_limit_without_content_length_never_calls_service(self, app, client, monkeypatch):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        calls = []
        monkeypatch.setattr(
            ProductLaunchCodeService, "consume_launch_code",
            staticmethod(lambda *a, **k: calls.append((a, k))),
        )

        oversized_body = json.dumps({"code": "z" * 5000}).encode("utf-8")
        response = _post_without_content_length(
            app, oversized_body,
            headers=_basic_auth_header(chain["installation_public_id"], chain["secret"]),
        )

        assert response.status_code == 400
        assert calls == []

    def test_body_over_limit_without_content_length_never_burns_a_real_code(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        oversized_body = json.dumps({"code": chain["code"] + "w" * 5000}).encode("utf-8")
        _post_without_content_length(
            app, oversized_body,
            headers=_basic_auth_header(chain["installation_public_id"], chain["secret"]),
        )

        with app.app_context():
            launch_code = ProductLaunchCode.query.filter_by(
                organization_product_installation_id=chain["installation_id"]
            ).first()
            assert launch_code.consumed_at is None


# Autenticação Basic (401) ---------------------------------------------------

class TestConsumeLaunchCodeBasicAuth:
    def test_missing_authorization_header_returns_401(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        response = client.post(ROUTE, json={"code": chain["code"]})
        assert response.status_code == 401
        assert response.get_json() == {"error": "invalid_credentials_or_code"}

    def test_bearer_scheme_returns_401(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        response = client.post(
            ROUTE,
            json={"code": chain["code"]},
            headers={"Authorization": "Bearer algum-token"},
        )
        assert response.status_code == 401
        assert response.get_json() == {"error": "invalid_credentials_or_code"}

    def test_malformed_base64_returns_401(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        response = client.post(
            ROUTE,
            json={"code": chain["code"]},
            headers={"Authorization": "Basic ###nao-e-base64-valido###"},
        )
        assert response.status_code == 401
        assert response.get_json() == {"error": "invalid_credentials_or_code"}

    def test_missing_password_in_basic_auth_returns_401(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        raw = f"{chain['installation_public_id']}:".encode("utf-8")
        response = client.post(
            ROUTE,
            json={"code": chain["code"]},
            headers={"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")},
        )
        assert response.status_code == 401
        assert response.get_json() == {"error": "invalid_credentials_or_code"}

    def test_wrong_secret_returns_401(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        response = _post(client, chain, secret="segredo-totalmente-errado")
        assert response.status_code == 401
        assert response.get_json() == {"error": "invalid_credentials_or_code"}

    def test_unknown_public_id_returns_401(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        response = _post(client, chain, public_id=str(uuid.uuid4()))
        assert response.status_code == 401
        assert response.get_json() == {"error": "invalid_credentials_or_code"}

    def test_401_response_has_no_store_cache_headers(self, app, client):
        response = client.post(ROUTE, json={"code": "qualquer"})
        assert response.status_code == 401
        assert response.headers.get("Cache-Control") == "no-store"
        assert response.headers.get("Pragma") == "no-cache"

    def test_authentication_failure_never_burns_a_real_code(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        _post(client, chain, secret="segredo-errado")

        with app.app_context():
            launch_code = ProductLaunchCode.query.filter_by(
                organization_product_installation_id=chain["installation_id"]
            ).first()
            assert launch_code.consumed_at is None


# Métodos não permitidos ------------------------------------------------------

class TestConsumeLaunchCodeMethodNotAllowed:
    """Achado M1-3 da revisão técnica: a versão anterior deste teste só
    verificava o status 405, mascarando que a resposta real era a
    página HTML padrão do Flask, sem `Cache-Control`/`Pragma`. Corrigido
    registrando GET/PUT/PATCH/DELETE na própria rota (nunca via
    `@federation_bp.errorhandler(405)`, comprovado empiricamente NUNCA
    chamado para um 405 de roteamento) e checando `request.method`
    dentro da view - o endpoint continua funcionalmente somente-POST."""

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_disallowed_methods_return_full_json_contract(self, client, method):
        response = getattr(client, method)(ROUTE)

        assert response.status_code == 405
        assert response.is_json
        assert response.get_json() == {"error": "invalid_request"}
        assert response.headers.get("Cache-Control") == "no-store"
        assert response.headers.get("Pragma") == "no-cache"
        assert "WWW-Authenticate" not in response.headers

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_disallowed_methods_never_reach_the_service(self, client, monkeypatch, method):
        calls = []
        monkeypatch.setattr(
            ProductLaunchCodeService, "consume_launch_code",
            staticmethod(lambda *a, **k: calls.append((a, k))),
        )

        response = getattr(client, method)(ROUTE)

        assert response.status_code == 405
        assert calls == []

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_disallowed_methods_have_no_database_effect(self, app, client, method):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        getattr(client, method)(ROUTE)

        with app.app_context():
            launch_code = ProductLaunchCode.query.filter_by(
                organization_product_installation_id=chain["installation_id"]
            ).first()
            assert launch_code.consumed_at is None
            assert AuditLog.query.filter_by(
                action="organization_product_installation.launch_code_consumed"
            ).count() == 0

    def test_other_route_405_is_unaffected_by_this_contract(self, client, monkeypatch):
        # Não-regressão: o contrato JSON/cache do 405 é local a esta
        # rota - outra rota qualquer que já retorna 405 (`/api/v1/leads`,
        # Issue #43/#47) continua com o comportamento padrão do Flask
        # (HTML, sem `Cache-Control`), nunca herdando o handler ou o
        # registro de métodos desta rota.
        monkeypatch.setenv("HUB_API_KEY", "chave-sintetica-nao-regressao-issue67")
        response = client.get("/api/v1/leads")

        assert response.status_code == 405
        assert response.content_type == "text/html; charset=utf-8"
        assert response.headers.get("Cache-Control") != "no-store"


# Isenção de CSRF (e não-propagação a outras rotas) --------------------------

class TestConsumeLaunchCodeCSRFExemption:
    def test_endpoint_works_without_csrf_token_or_session(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        response = _post(client, chain)
        assert response.status_code == 200

    def test_valid_session_cookie_alone_is_not_accepted_as_credential(self, app, client, get_csrf_token):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()
            _create_user(email="usuario.sessao.federation@example.test")

        client.post("/login", data={
            "email": "usuario.sessao.federation@example.test",
            "password": SYNTHETIC_PASSWORD,
            "csrf_token": get_csrf_token(client),
        })

        response = client.post(ROUTE, json={"code": chain["code"]})
        assert response.status_code == 401

    def test_other_route_still_requires_csrf_token(self, client, app, get_csrf_token):
        # Não-regressão: a isenção de `@csrf.exempt` é por função de view,
        # nunca global - /login continua exigindo token válido mesmo
        # depois de registrado o blueprint federado.
        with app.app_context():
            _create_user(email="admin.nao.regressao.federation@example.test")

        response = client.post("/login", data={
            "email": "admin.nao.regressao.federation@example.test",
            "password": SYNTHETIC_PASSWORD,
        })
        assert response.status_code == 400


# Ausência de vazamento -------------------------------------------------------

class TestConsumeLaunchCodeNoLeakage:
    def test_success_response_never_contains_secret_code_or_hash(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        response = _post(client, chain)
        body_text = response.get_data(as_text=True)

        assert response.status_code == 200
        assert chain["secret"] not in body_text
        assert chain["code"] not in body_text

    def test_error_responses_never_leak_credential_or_code(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        wrong_secret = "segredo-forjado-nao-deve-vazar"
        response = _post(client, chain, secret=wrong_secret)
        body_text = response.get_data(as_text=True)

        assert response.status_code == 401
        assert chain["secret"] not in body_text
        assert wrong_secret not in body_text
        assert chain["code"] not in body_text
        assert "Traceback" not in body_text


# Falha operacional (500) -----------------------------------------------------

class TestConsumeLaunchCodeOperationalFailure:
    def test_unexpected_service_failure_returns_500_generic_body(self, app, client, monkeypatch):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        def _raise(*args, **kwargs):
            raise RuntimeError("falha sintetica issue 67")

        monkeypatch.setattr(ProductLaunchCodeService, "consume_launch_code", staticmethod(_raise))

        response = _post(client, chain)

        assert response.status_code == 500
        assert response.get_json() == {"error": "internal_error"}
        body_text = response.get_data(as_text=True)
        assert "falha sintetica issue 67" not in body_text
        assert "Traceback" not in body_text

    def test_500_response_has_no_store_cache_headers(self, app, client, monkeypatch):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()

        monkeypatch.setattr(
            ProductLaunchCodeService, "consume_launch_code",
            staticmethod(lambda *a, **k: (_ for _ in ()).throw(RuntimeError("falha"))),
        )

        response = _post(client, chain)
        assert response.status_code == 500
        assert response.headers.get("Cache-Control") == "no-store"
        assert response.headers.get("Pragma") == "no-cache"


# Caminho C através da camada HTTP (Achado M1-2) ------------------------------

class TestConsumeLaunchCodePathCViaHTTP:
    """Achado M1-2 da revisão técnica: o Caminho C (código reivindicado
    atomicamente, mas autorização revogada na revalidação subsequente)
    só era coberto no service - nenhum teste HTTP provava a integração
    completa (401 uniforme, headers, ausência de identidade/auditoria,
    persistência de `consumed_at`, segunda tentativa). Usa a
    ORGANIZAÇÃO desativada APÓS a emissão do código como gatilho -
    nunca instalação inativa, que é rejeitada antes, na própria
    autenticação da credencial (Caminho A), sem alcançar o Caminho C."""

    def test_first_attempt_returns_401_burns_code_without_identity_or_audit(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()
            organization = Organization.query.get(uuid.UUID(chain["organization_id"]))
            organization.is_active = False
            db.session.commit()
            audit_before = AuditLog.query.filter_by(
                action="organization_product_installation.launch_code_consumed"
            ).count()

        response = _post(client, chain)

        assert response.status_code == 401
        assert response.get_json() == {"error": "invalid_credentials_or_code"}
        assert set(response.get_json().keys()) == {"error"}
        assert response.headers.get("Cache-Control") == "no-store"
        assert response.headers.get("Pragma") == "no-cache"
        assert "WWW-Authenticate" not in response.headers

        with app.app_context():
            launch_code = ProductLaunchCode.query.filter_by(
                organization_product_installation_id=chain["installation_id"]
            ).first()
            assert launch_code.consumed_at is not None
            assert AuditLog.query.filter_by(
                action="organization_product_installation.launch_code_consumed"
            ).count() == audit_before

    def test_second_attempt_is_indistinguishable_and_never_replaces_consumed_at(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()
            organization = Organization.query.get(uuid.UUID(chain["organization_id"]))
            organization.is_active = False
            db.session.commit()
            audit_before = AuditLog.query.filter_by(
                action="organization_product_installation.launch_code_consumed"
            ).count()

        first = _post(client, chain)
        assert first.status_code == 401

        with app.app_context():
            consumed_at_after_first = ProductLaunchCode.query.filter_by(
                organization_product_installation_id=chain["installation_id"]
            ).first().consumed_at

        second = _post(client, chain)

        assert second.status_code == 401
        assert second.get_json() == {"error": "invalid_credentials_or_code"}
        assert set(second.get_json().keys()) == {"error"}
        assert second.headers.get("Cache-Control") == "no-store"
        assert second.headers.get("Pragma") == "no-cache"
        assert "WWW-Authenticate" not in second.headers

        with app.app_context():
            launch_code = ProductLaunchCode.query.filter_by(
                organization_product_installation_id=chain["installation_id"]
            ).first()
            # `consumed_at` não foi substituído pela segunda tentativa -
            # continua exatamente o valor commitado na primeira.
            assert launch_code.consumed_at == consumed_at_after_first
            assert AuditLog.query.filter_by(
                action="organization_product_installation.launch_code_consumed"
            ).count() == audit_before


# Credencial na janela de rotação através da camada HTTP (Cobertura B1) -------

class TestConsumeLaunchCodeCredentialGracePeriodViaHTTP:
    """Cobertura B1 da revisão técnica: `test_credential_within_grace_period_still_succeeds`
    (`tests/test_product_launch_code_service.py`) já prova isso no
    service; este teste prova a mesma integração através da rota HTTP
    real, sem alterar a política de rotação da Issue #64 (a credencial
    anterior continua aceita durante
    `INSTALLATION_CREDENTIAL_ROTATION_GRACE_SECONDS`)."""

    def test_previous_credential_within_grace_period_succeeds_via_route(self, app, client):
        with app.app_context():
            chain = _build_chain_with_credential_and_code()
            old_secret = chain["secret"]
            InstallationCredentialService.rotate_credential(
                chain["installation_id"], actor_user_id=None
            )
            audit_before = AuditLog.query.filter_by(
                action="organization_product_installation.launch_code_consumed"
            ).count()

        response = _post(client, chain, secret=old_secret)

        assert response.status_code == 200
        assert set(response.get_json().keys()) == _EXPECTED_FIELDS

        with app.app_context():
            assert AuditLog.query.filter_by(
                action="organization_product_installation.launch_code_consumed"
            ).count() == audit_before + 1
