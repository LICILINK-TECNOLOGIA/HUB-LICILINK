"""Fronteira de implantação (`app/deployment.py` e `app/wsgi.py`).

Tudo aqui usa valores sintéticos e SQLite em memória: nenhuma conexão real
é aberta, e `create_app()` em si não é alterado nem testado de novo.
"""
import importlib
import logging
import sys

import pytest
from werkzeug.middleware.proxy_fix import ProxyFix

from app import deployment
from app.deployment import (
    PROXY_FIX_MAX_TRUSTED,
    PROXY_FIX_X_FOR_VAR,
    PROXY_FIX_X_PROTO_VAR,
    create_deployed_app,
    load_deployment_settings,
    parse_trusted_proxy_count,
    resolve_deployed_environment,
)

SYNTHETIC_SECRET_KEY = "s" * 40
# Marcador sintético usado para provar que nenhuma mensagem repete o valor recebido.
MARKER = "valor-sintetico-xq7zk"

VALID_ENV = {
    "FLASK_ENV": "staging",
    "SECRET_KEY": SYNTHETIC_SECRET_KEY,
    "DATABASE_URL": "sqlite:///:memory:",
    PROXY_FIX_X_FOR_VAR: "0",
    PROXY_FIX_X_PROTO_VAR: "0",
    "EMAIL_PROVIDER": "console",
    "L_KALENDER_URL": "https://kalender.exemplo.test",
    "L_GEDO_URL": "https://gedo.exemplo.test",
    "L_HUNT_URL": "https://hunt.exemplo.test",
}
DEPLOYMENT_VARS = (
    *VALID_ENV, "RESEND_API_KEY", "EMAIL_FROM", "EMAIL_REPLY_TO",
)


@pytest.fixture
def deploy_env(monkeypatch):
    """Ambiente sintético completo e válido; devolve uma função para ajustar
    (valor `None` remove a variável)."""
    for name in DEPLOYMENT_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in VALID_ENV.items():
        monkeypatch.setenv(name, value)

    def _set(**overrides):
        for name, value in overrides.items():
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)

    return _set


def _fail_if_app_is_created(monkeypatch):
    def _fail(*args, **kwargs):
        raise AssertionError("create_app não deveria ser chamado com a implantação inválida")

    monkeypatch.setattr(deployment, "create_app", _fail)


class TestDeployedEnvironment:
    @pytest.mark.parametrize("value", ["staging", "production", "  STAGING  ", "Production"])
    def test_aceita_so_staging_e_production(self, deploy_env, value):
        deploy_env(FLASK_ENV=value)
        assert resolve_deployed_environment() == value.strip().lower()

    @pytest.mark.parametrize("value", [None, "", "   ", "development", "testing", MARKER])
    def test_ausente_ou_outro_valor_falha_sem_cair_em_development(self, deploy_env, monkeypatch, value):
        deploy_env(FLASK_ENV=value)
        _fail_if_app_is_created(monkeypatch)
        with pytest.raises(RuntimeError) as excinfo:
            create_deployed_app()
        message = str(excinfo.value)
        assert "FLASK_ENV" in message
        assert MARKER not in message


class TestTrustedProxyCount:
    @pytest.mark.parametrize("raw", [str(n) for n in range(PROXY_FIX_MAX_TRUSTED + 1)])
    def test_valores_da_faixa_sao_aceitos(self, raw):
        assert parse_trusted_proxy_count(raw, PROXY_FIX_X_FOR_VAR) == int(raw)

    @pytest.mark.parametrize("raw", [
        None, "", "   ", "-1", "+1", "1.0", "1e0", "dois", "١", "²", "0x1", "01", "10",
        str(PROXY_FIX_MAX_TRUSTED + 1), "9" * 5000, MARKER,
    ])
    def test_ausente_invalido_ou_fora_da_faixa_falha(self, raw):
        with pytest.raises(RuntimeError) as excinfo:
            parse_trusted_proxy_count(raw, PROXY_FIX_X_FOR_VAR)
        assert PROXY_FIX_X_FOR_VAR in str(excinfo.value)

    @pytest.mark.parametrize("raw", ["-1", "1.0", "dois", "0x1", "9" * 5000, MARKER])
    def test_mensagem_nunca_repete_o_valor_recebido(self, raw):
        with pytest.raises(RuntimeError) as excinfo:
            parse_trusted_proxy_count(raw, PROXY_FIX_X_FOR_VAR)
        assert raw not in str(excinfo.value)


class TestStagingRefusesToStart:
    @pytest.mark.parametrize("var_name", [
        PROXY_FIX_X_FOR_VAR, PROXY_FIX_X_PROTO_VAR, "EMAIL_PROVIDER",
        "L_KALENDER_URL", "L_GEDO_URL", "L_HUNT_URL",
    ])
    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_variavel_obrigatoria_ausente_impede_a_inicializacao(
        self, deploy_env, monkeypatch, var_name, value,
    ):
        deploy_env(**{var_name: value})
        _fail_if_app_is_created(monkeypatch)
        with pytest.raises(RuntimeError) as excinfo:
            create_deployed_app()
        assert var_name in str(excinfo.value)

    @pytest.mark.parametrize("var_name,value", [
        (PROXY_FIX_X_FOR_VAR, "dois"),
        (PROXY_FIX_X_FOR_VAR, "-1"),
        (PROXY_FIX_X_FOR_VAR, str(PROXY_FIX_MAX_TRUSTED + 1)),
        (PROXY_FIX_X_PROTO_VAR, "1.5"),
        (PROXY_FIX_X_PROTO_VAR, MARKER),
        ("EMAIL_PROVIDER", "smtp-" + MARKER),
        ("L_GEDO_URL", "http://gedo." + MARKER + ".test"),
        ("L_GEDO_URL", "https://usuario:" + MARKER + "@gedo.exemplo.test"),
        ("L_KALENDER_URL", MARKER),
        ("L_HUNT_URL", "https://"),
    ])
    def test_valor_invalido_impede_a_inicializacao_sem_repetir_o_valor(
        self, deploy_env, monkeypatch, var_name, value,
    ):
        deploy_env(**{var_name: value})
        _fail_if_app_is_created(monkeypatch)
        with pytest.raises(RuntimeError) as excinfo:
            create_deployed_app()
        message = str(excinfo.value)
        assert var_name in message
        assert MARKER not in message
        assert value not in message

    def test_todos_os_problemas_aparecem_juntos(self, deploy_env):
        deploy_env(**{PROXY_FIX_X_FOR_VAR: None, "EMAIL_PROVIDER": None, "L_HUNT_URL": None})
        with pytest.raises(RuntimeError) as excinfo:
            load_deployment_settings()
        message = str(excinfo.value)
        for var_name in (PROXY_FIX_X_FOR_VAR, "EMAIL_PROVIDER", "L_HUNT_URL"):
            assert var_name in message

    @pytest.mark.parametrize("missing", ["RESEND_API_KEY", "EMAIL_FROM"])
    def test_resend_exige_credencial_e_remetente(self, deploy_env, monkeypatch, missing):
        deploy_env(EMAIL_PROVIDER="resend", RESEND_API_KEY=MARKER, EMAIL_FROM="hub@exemplo.test")
        deploy_env(**{missing: None})
        _fail_if_app_is_created(monkeypatch)
        with pytest.raises(RuntimeError) as excinfo:
            create_deployed_app()
        message = str(excinfo.value)
        assert missing in message
        assert MARKER not in message

    @pytest.mark.parametrize("var_name", ["SECRET_KEY", "DATABASE_URL"])
    def test_exigencias_ja_existentes_da_factory_continuam_valendo(self, deploy_env, var_name):
        deploy_env(**{var_name: None})
        with pytest.raises(RuntimeError) as excinfo:
            create_deployed_app()
        assert var_name in str(excinfo.value)

    def test_production_tem_as_mesmas_exigencias(self, deploy_env, monkeypatch):
        deploy_env(FLASK_ENV="production", **{PROXY_FIX_X_FOR_VAR: None})
        _fail_if_app_is_created(monkeypatch)
        with pytest.raises(RuntimeError) as excinfo:
            create_deployed_app()
        assert PROXY_FIX_X_FOR_VAR in str(excinfo.value)


class TestDeployedAppCreation:
    def test_configuracao_valida_cria_a_app_de_staging(self, deploy_env):
        app = create_deployed_app()
        assert app.config["IS_PRODUCTION"] is True
        assert app.config["DEBUG"] is False
        assert app.config["SESSION_COOKIE_SECURE"] is True
        assert app.config["EMAIL_PROVIDER"] == "console"

    def test_urls_dos_produtos_vem_do_ambiente_e_nao_do_padrao_embutido(self, deploy_env):
        app = create_deployed_app()
        for var_name in ("L_KALENDER_URL", "L_GEDO_URL", "L_HUNT_URL"):
            assert app.config[var_name] == VALID_ENV[var_name]
            assert "licilink.com.br" not in app.config[var_name]

    def test_zero_declarado_nao_instala_o_proxy_fix(self, deploy_env):
        app = create_deployed_app()
        assert not isinstance(app.wsgi_app, ProxyFix)
        assert (app.config["PROXY_FIX_X_FOR"], app.config["PROXY_FIX_X_PROTO"]) == (0, 0)

    def test_quantidades_separadas_e_sem_host_porta_ou_prefixo(self, deploy_env):
        deploy_env(**{PROXY_FIX_X_FOR_VAR: "2", PROXY_FIX_X_PROTO_VAR: "1"})
        app = create_deployed_app()
        proxy_fix = app.wsgi_app
        assert isinstance(proxy_fix, ProxyFix)
        assert (proxy_fix.x_for, proxy_fix.x_proto) == (2, 1)
        assert (proxy_fix.x_host, proxy_fix.x_port, proxy_fix.x_prefix) == (0, 0, 0)

    def test_health_responde_na_app_de_staging(self, deploy_env):
        response = create_deployed_app().test_client().get("/health")
        assert response.status_code == 200
        assert response.get_json() == {"status": "healthy"}


class TestStartupWarnings:
    def _warnings(self, caplog):
        return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]

    def test_zero_em_staging_gera_aviso_para_cada_cabecalho(self, deploy_env, caplog):
        with caplog.at_level(logging.WARNING):
            create_deployed_app()
        mensagens = self._warnings(caplog)
        assert any(m.startswith(PROXY_FIX_X_FOR_VAR + "=0") for m in mensagens)
        assert any(m.startswith(PROXY_FIX_X_PROTO_VAR + "=0") for m in mensagens)

    def test_console_em_staging_gera_aviso(self, deploy_env, caplog):
        with caplog.at_level(logging.WARNING):
            create_deployed_app()
        assert any(m.startswith("EMAIL_PROVIDER=console") for m in self._warnings(caplog))

    def test_sem_zero_e_sem_console_nao_ha_aviso(self, deploy_env, caplog):
        deploy_env(
            EMAIL_PROVIDER="resend", RESEND_API_KEY="chave-sintetica", EMAIL_FROM="hub@exemplo.test",
            **{PROXY_FIX_X_FOR_VAR: "1", PROXY_FIX_X_PROTO_VAR: "1"},
        )
        with caplog.at_level(logging.WARNING):
            app = create_deployed_app()
        mensagens = self._warnings(caplog)
        assert not any(PROXY_FIX_X_FOR_VAR in m or PROXY_FIX_X_PROTO_VAR in m for m in mensagens)
        assert not any("EMAIL_PROVIDER" in m for m in mensagens)
        assert app.config["RESEND_API_KEY"] == "chave-sintetica"


class TestWsgiModule:
    def _import_wsgi(self):
        # Importação sempre nova, e sem deixar o módulo (nem o atributo no
        # pacote) para os outros testes.
        import app as app_package

        sys.modules.pop("app.wsgi", None)
        try:
            return importlib.import_module("app.wsgi")
        finally:
            sys.modules.pop("app.wsgi", None)
            app_package.__dict__.pop("wsgi", None)

    def test_importar_com_configuracao_valida_expoe_application(self, deploy_env):
        module = self._import_wsgi()
        assert module.application.config["IS_PRODUCTION"] is True

    @pytest.mark.parametrize("value", [None, "development", "testing"])
    def test_importar_sem_flask_env_de_implantacao_falha(self, deploy_env, value):
        deploy_env(FLASK_ENV=value)
        with pytest.raises(RuntimeError):
            self._import_wsgi()
