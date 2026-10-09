"""Cabeçalhos encaminhados por proxy reverso (`app.deployment.apply_proxy_fix`).

Cobre o que muda para o limitador de login (chave = `request.remote_addr`) e
para a auditoria (`AuditService` grava `request.remote_addr`), com a
quantidade de proxies confiáveis desligada e ligada.

Endereços das faixas reservadas para documentação (RFC 5737); nenhum real.
"""
import pytest
from flask import jsonify, request

from app.deployment import apply_proxy_fix
from app.models import AuditLog
from app.services.audit_service import AuditService

PROXY = "192.0.2.10"        # quem abre a conexão com o HUB (REMOTE_ADDR)
INNER_PROXY = "192.0.2.20"  # um segundo proxy da cadeia
CLIENT_A = "203.0.113.10"
CLIENT_B = "203.0.113.20"
FORGED = "198.51.100.99"    # valor que um cliente pode ter posto no cabeçalho

PROBE_ACTION = "teste.proxy_fix.sonda"


def _add_probe(app):
    """Rota só de teste: grava uma entrada de auditoria pelo serviço real e
    devolve o que a aplicação enxerga da requisição."""

    def probe():
        entry = AuditService.log_action(PROBE_ACTION)
        return jsonify(
            remote_addr=request.remote_addr,
            audit_ip=entry.ip_address,
            scheme=request.scheme,
            is_secure=request.is_secure,
            host=request.host,
            script_root=request.script_root,
            url=request.url,
        )

    app.add_url_rule("/__sonda", "sonda_proxy_fix", probe)


def _probe(client, headers=None):
    response = client.get(
        "/__sonda", headers=headers or {}, environ_overrides={"REMOTE_ADDR": PROXY},
    )
    assert response.status_code == 200
    return response.get_json()


def _post_login(client, get_csrf_token, headers=None, **extra_environ):
    return client.post(
        "/login",
        data={
            "email": "sintetico.proxyfix@example.com",
            "password": "x",
            "csrf_token": get_csrf_token(client, "/login"),
        },
        headers=headers or {},
        environ_overrides={"REMOTE_ADDR": PROXY, **extra_environ},
    )


@pytest.fixture
def proxied(app):
    """Devolve uma função que liga o ProxyFix na app de teste, instala a
    sonda e devolve um cliente."""

    def _build(*, x_for, x_proto):
        apply_proxy_fix(app, x_for=x_for, x_proto=x_proto)
        _add_probe(app)
        return app.test_client()

    return _build


class TestDesligado:
    def test_cabecalhos_encaminhados_sao_ignorados(self, proxied):
        client = proxied(x_for=0, x_proto=0)
        seen = _probe(client, {
            "X-Forwarded-For": CLIENT_A,
            "X-Forwarded-Proto": "https",
        })
        assert seen["remote_addr"] == PROXY
        assert seen["audit_ip"] == PROXY
        assert seen["scheme"] == "http"
        assert seen["is_secure"] is False

    def test_auditoria_grava_o_endereco_do_proxy(self, proxied, app):
        client = proxied(x_for=0, x_proto=0)
        _probe(client, {"X-Forwarded-For": CLIENT_A})
        entry = AuditLog.query.filter_by(action=PROBE_ACTION).one()
        assert entry.ip_address == PROXY

    def test_todos_os_clientes_dividem_o_mesmo_limite_de_login(self, proxied, get_csrf_token):
        client = proxied(x_for=0, x_proto=0)
        for n in range(5):
            response = _post_login(
                client, get_csrf_token, {"X-Forwarded-For": f"203.0.113.{100 + n}"},
            )
            assert response.status_code == 200
        # Sexta tentativa, de um "cliente" que nunca tentou: bloqueada, porque
        # a chave do limitador é o endereço do proxy.
        assert _post_login(
            client, get_csrf_token, {"X-Forwarded-For": CLIENT_B},
        ).status_code == 429


class TestLigado:
    def test_um_proxy_usa_o_ultimo_endereco_do_cabecalho(self, proxied):
        client = proxied(x_for=1, x_proto=0)
        seen = _probe(client, {"X-Forwarded-For": CLIENT_A})
        assert seen["remote_addr"] == CLIENT_A
        assert seen["audit_ip"] == CLIENT_A

    def test_auditoria_grava_o_endereco_do_cliente(self, proxied, app):
        client = proxied(x_for=1, x_proto=0)
        _probe(client, {"X-Forwarded-For": CLIENT_A})
        entry = AuditLog.query.filter_by(action=PROBE_ACTION).one()
        assert entry.ip_address == CLIENT_A

    def test_cada_cliente_tem_o_proprio_limite_de_login(self, proxied, get_csrf_token):
        client = proxied(x_for=1, x_proto=0)
        for _ in range(5):
            assert _post_login(
                client, get_csrf_token, {"X-Forwarded-For": CLIENT_A},
            ).status_code == 200
        assert _post_login(
            client, get_csrf_token, {"X-Forwarded-For": CLIENT_A},
        ).status_code == 429
        # Outro cliente, pelo mesmo proxy, não herda o bloqueio.
        assert _post_login(
            client, get_csrf_token, {"X-Forwarded-For": CLIENT_B},
        ).status_code == 200

    def test_valor_forjado_pelo_cliente_nao_muda_a_chave_do_limitador(self, proxied, get_csrf_token):
        # O proxy confiável acrescenta o endereço real à DIREITA. O que o
        # cliente escreveu à esquerda varia a cada tentativa e é ignorado.
        client = proxied(x_for=1, x_proto=0)
        for n in range(5):
            assert _post_login(
                client, get_csrf_token, {"X-Forwarded-For": f"198.51.100.{n}, {CLIENT_A}"},
            ).status_code == 200
        assert _post_login(
            client, get_csrf_token, {"X-Forwarded-For": f"{FORGED}, {CLIENT_A}"},
        ).status_code == 429


class TestCadeiaComMaisDeUmEndereco:
    CHAIN = f"{FORGED}, {CLIENT_A}, {INNER_PROXY}"

    @pytest.mark.parametrize("x_for,expected", [
        (1, INNER_PROXY),  # só o proxy que conecta é confiável
        (2, CLIENT_A),     # dois proxies confiáveis
        (3, FORGED),       # três: quantidade maior que a real aceita o valor forjado
    ])
    def test_obedece_exatamente_a_quantidade_configurada(self, proxied, x_for, expected):
        client = proxied(x_for=x_for, x_proto=0)
        seen = _probe(client, {"X-Forwarded-For": self.CHAIN})
        assert seen["remote_addr"] == expected
        assert seen["audit_ip"] == expected

    def test_cadeia_mais_curta_que_a_quantidade_mantem_o_endereco_da_conexao(self, proxied):
        client = proxied(x_for=3, x_proto=0)
        seen = _probe(client, {"X-Forwarded-For": f"{CLIENT_A}, {INNER_PROXY}"})
        assert seen["remote_addr"] == PROXY

    def test_sem_cabecalho_mantem_o_endereco_da_conexao(self, proxied):
        client = proxied(x_for=2, x_proto=0)
        assert _probe(client)["remote_addr"] == PROXY


class TestProtocoloEncaminhado:
    def test_https_encaminhado_e_reconhecido(self, proxied):
        client = proxied(x_for=0, x_proto=1)
        seen = _probe(client, {"X-Forwarded-Proto": "https"})
        assert seen["scheme"] == "https"
        assert seen["is_secure"] is True
        # x_for continua desligado: são configurações separadas.
        assert seen["remote_addr"] == PROXY

    def test_x_for_ligado_nao_liga_o_protocolo(self, proxied):
        client = proxied(x_for=1, x_proto=0)
        seen = _probe(client, {"X-Forwarded-For": CLIENT_A, "X-Forwarded-Proto": "https"})
        assert seen["remote_addr"] == CLIENT_A
        assert seen["scheme"] == "http"

    def test_formulario_com_csrf_funciona_sobre_https_encaminhado(self, proxied, get_csrf_token):
        client = proxied(x_for=1, x_proto=1)
        https = {"X-Forwarded-For": CLIENT_A, "X-Forwarded-Proto": "https"}
        page = client.get("/login", headers=https, environ_overrides={"REMOTE_ADDR": PROXY})
        assert page.status_code == 200
        # Sobre HTTPS o Flask-WTF também confere o Referer, que o navegador
        # envia num formulário da mesma origem.
        response = _post_login(
            client, get_csrf_token, {**https, "Referer": "https://localhost/login"},
        )
        assert response.status_code == 200
        assert "Credenciais inválidas." in response.get_data(as_text=True)

    def test_sem_token_csrf_continua_recusado_sobre_https_encaminhado(self, proxied):
        client = proxied(x_for=1, x_proto=1)
        response = client.post(
            "/login",
            data={"email": "sintetico.proxyfix@example.com", "password": "x"},
            headers={
                "X-Forwarded-For": CLIENT_A, "X-Forwarded-Proto": "https",
                "Referer": "https://localhost/login",
            },
            environ_overrides={"REMOTE_ADDR": PROXY},
        )
        assert response.status_code == 400


class TestHostPortaEPrefixoNuncaMudam:
    FORWARDED = {
        "X-Forwarded-For": CLIENT_A,
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "outro-host.exemplo.test",
        "X-Forwarded-Port": "8443",
        "X-Forwarded-Prefix": "/prefixo",
    }

    @pytest.mark.parametrize("x_for,x_proto", [(0, 0), (1, 1), (5, 5)])
    def test_nenhum_cabecalho_altera_host_porta_ou_prefixo(self, proxied, x_for, x_proto):
        client = proxied(x_for=x_for, x_proto=x_proto)
        seen = _probe(client, self.FORWARDED)
        assert seen["host"] == "localhost"
        assert seen["script_root"] == ""
        assert "outro-host" not in seen["url"]
        assert "8443" not in seen["url"]
        assert "/prefixo" not in seen["url"]


class TestApplyProxyFixRecusaValoresForaDaFaixa:
    @pytest.mark.parametrize("x_for,x_proto", [(-1, 0), (0, 6), ("1", 0), (True, 0), (None, 0), (1.0, 1)])
    def test_valor_invalido_levanta_erro(self, app, x_for, x_proto):
        with pytest.raises(ValueError):
            apply_proxy_fix(app, x_for=x_for, x_proto=x_proto)
