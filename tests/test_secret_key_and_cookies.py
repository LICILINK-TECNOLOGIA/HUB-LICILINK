import pytest
from flask import Flask

from app import create_app
from app.config import (
    DevelopmentConfig,
    TestingConfig,
    StagingConfig,
    ProductionConfig,
    MIN_SECRET_KEY_LENGTH,
    configure_secret_key,
    config_by_name,
)


def _clear_secret_key_env(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)


def _bare_app_with_config(config_class):
    # App Flask mínima, sem inicializar SQLAlchemy/Migrate/Limiter/blueprints:
    # isola o teste na lógica de `configure_secret_key`, sem depender da
    # camada de banco (fora do escopo desta Issue).
    app = Flask(__name__)
    app.config.from_object(config_class)
    return app


class ProductionLikeSQLiteConfig(ProductionConfig):
    """Config exclusiva desta suíte de testes.

    Herda de ProductionConfig (IS_PRODUCTION=True, SESSION_COOKIE_SECURE=True,
    validação obrigatória de SECRET_KEY) mas troca o banco por SQLite em
    memória, para exercitar create_app() por completo sem exigir PostgreSQL
    real. Nunca é usada fora dos testes e nunca é registrada permanentemente
    em config_by_name (ver fixture `production_like_config_name`).
    """

    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class TestSecretKeyProductionValidation:
    def test_production_without_secret_key_fails(self, monkeypatch):
        _clear_secret_key_env(monkeypatch)
        with pytest.raises(RuntimeError):
            create_app("production")

    def test_staging_without_secret_key_fails(self, monkeypatch):
        _clear_secret_key_env(monkeypatch)
        with pytest.raises(RuntimeError):
            create_app("staging")

    def test_production_rejects_default_secret_key(self, monkeypatch):
        # Valor sintético: o antigo fallback hardcoded, testado apenas por nome/formato.
        monkeypatch.setenv("SECRET_KEY", "default-secret-key")
        with pytest.raises(RuntimeError):
            create_app("production")

    def test_production_rejects_env_example_placeholder(self, monkeypatch):
        # Valor sintético: o mesmo placeholder documentado em .env.example.
        monkeypatch.setenv("SECRET_KEY", "change-me-in-development")
        with pytest.raises(RuntimeError):
            create_app("production")

    def test_production_rejects_short_secret_key(self, monkeypatch):
        short_key = "synthetic-short"  # valor sintético, propositalmente curto
        assert len(short_key) < MIN_SECRET_KEY_LENGTH
        monkeypatch.setenv("SECRET_KEY", short_key)
        with pytest.raises(RuntimeError):
            create_app("production")

    def test_production_accepts_valid_synthetic_secret_key(self, monkeypatch):
        synthetic_key = "s" * MIN_SECRET_KEY_LENGTH  # valor sintético exclusivo deste teste
        monkeypatch.setenv("SECRET_KEY", synthetic_key)
        app = _bare_app_with_config(ProductionConfig)
        configure_secret_key(app)
        assert app.config["SECRET_KEY"] == synthetic_key


class TestSecretKeyDevelopmentGeneration:
    def test_development_generates_nonempty_temp_key_without_external_secret(self, monkeypatch):
        _clear_secret_key_env(monkeypatch)
        app = _bare_app_with_config(DevelopmentConfig)
        configure_secret_key(app)
        secret_key = app.config["SECRET_KEY"]
        assert secret_key
        assert isinstance(secret_key, str)
        assert len(secret_key) >= MIN_SECRET_KEY_LENGTH

    def test_development_independent_apps_do_not_need_shared_temp_key(self, monkeypatch):
        _clear_secret_key_env(monkeypatch)
        app_a = _bare_app_with_config(DevelopmentConfig)
        app_b = _bare_app_with_config(DevelopmentConfig)
        configure_secret_key(app_a)
        configure_secret_key(app_b)
        assert app_a.config["SECRET_KEY"]
        assert app_b.config["SECRET_KEY"]
        assert app_a.config["SECRET_KEY"] != app_b.config["SECRET_KEY"]


class TestSecretKeyDynamicReadPerCall:
    def test_configure_secret_key_reads_current_env_value_not_import_time_value(self, monkeypatch):
        # Prova de regressão: configure_secret_key() precisa ler os.getenv
        # no momento da chamada, e não um valor congelado na importação de
        # app.config (o mesmo problema identificado em SQLALCHEMY_DATABASE_URI,
        # que aqui não se aplica a SECRET_KEY).
        monkeypatch.setenv("SECRET_KEY", "valor-sintetico-numero-um-1234567890")
        app_1 = _bare_app_with_config(ProductionConfig)
        configure_secret_key(app_1)
        assert app_1.config["SECRET_KEY"] == "valor-sintetico-numero-um-1234567890"

        monkeypatch.setenv("SECRET_KEY", "valor-sintetico-numero-dois-abcdefghij")
        app_2 = _bare_app_with_config(ProductionConfig)
        configure_secret_key(app_2)
        assert app_2.config["SECRET_KEY"] == "valor-sintetico-numero-dois-abcdefghij"

        assert app_1.config["SECRET_KEY"] != app_2.config["SECRET_KEY"]


class TestFactoryProductionLikeIntegration:
    """Testa create_app() por completo (fábrica inteira, sem atalhos),
    usando uma config derivada de ProductionConfig só que com SQLite em
    memória, registrada temporariamente em config_by_name via monkeypatch
    e removida automaticamente ao final de cada teste.
    """

    @pytest.fixture
    def production_like_config_name(self, monkeypatch):
        name = "testing_production_like"
        monkeypatch.setitem(config_by_name, name, ProductionLikeSQLiteConfig)
        yield name
        # monkeypatch.setitem já remove a chave automaticamente no teardown,
        # pois ela não existia em config_by_name antes deste fixture.

    def test_missing_secret_key_fails_before_extension_initialization(
        self, monkeypatch, production_like_config_name
    ):
        _clear_secret_key_env(monkeypatch)

        from app.extensions import db

        def _fail_if_called(*args, **kwargs):
            raise AssertionError(
                "db.init_app não deveria ser chamado quando SECRET_KEY está ausente/inválida"
            )

        monkeypatch.setattr(db, "init_app", _fail_if_called)

        with pytest.raises(RuntimeError):
            create_app(production_like_config_name)

    def test_valid_synthetic_key_creates_full_app_with_secure_cookies(
        self, monkeypatch, production_like_config_name
    ):
        synthetic_key = "p" * MIN_SECRET_KEY_LENGTH  # valor sintético exclusivo deste teste
        monkeypatch.setenv("SECRET_KEY", synthetic_key)

        app = create_app(production_like_config_name)

        assert app.config["SECRET_KEY"] == synthetic_key
        assert app.config["IS_PRODUCTION"] is True
        assert app.config["SESSION_COOKIE_SECURE"] is True
        assert app.config["SESSION_COOKIE_HTTPONLY"] is True
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"

    def test_config_removed_from_registry_after_test(self):
        # Confirma que a config temporária não vaza para outros testes.
        assert "testing_production_like" not in config_by_name


class TestSecretKeyTesting:
    def test_testing_uses_its_own_explicit_key(self):
        app = create_app("testing")
        assert app.config["SECRET_KEY"] == TestingConfig.SECRET_KEY
        assert app.config["SECRET_KEY"] not in (
            "default-secret-key",
            "change-me-in-development",
        )


class TestCookieAttributesPerConfigClass:
    @pytest.mark.parametrize(
        "config_class", [DevelopmentConfig, TestingConfig, StagingConfig, ProductionConfig]
    )
    def test_httponly_and_samesite_are_always_set(self, config_class):
        assert config_class.SESSION_COOKIE_HTTPONLY is True
        assert config_class.SESSION_COOKIE_SAMESITE == "Lax"

    def test_secure_is_true_in_staging_and_production(self):
        assert StagingConfig.SESSION_COOKIE_SECURE is True
        assert ProductionConfig.SESSION_COOKIE_SECURE is True

    def test_secure_is_false_in_development_and_testing(self):
        assert DevelopmentConfig.SESSION_COOKIE_SECURE is False
        assert TestingConfig.SESSION_COOKIE_SECURE is False

    def test_is_production_flag_per_class(self):
        assert DevelopmentConfig.IS_PRODUCTION is False
        assert TestingConfig.IS_PRODUCTION is False
        assert StagingConfig.IS_PRODUCTION is True
        assert ProductionConfig.IS_PRODUCTION is True


class TestSetCookieHeaderRealResponse:
    def test_set_cookie_header_has_httponly_and_samesite(self, client):
        # Usa a rota /login já existente (sem alterá-la) com credenciais
        # sintéticas inválidas: isso é suficiente para o Flask gravar a
        # mensagem de flash na sessão e emitir um Set-Cookie real.
        response = client.post(
            "/login",
            data={
                "email": "usuario.sintetico.inexistente@example.com",
                "password": "senha-sintetica-de-teste",
            },
        )
        set_cookie_headers = response.headers.getlist("Set-Cookie")
        session_cookie = next(
            (h for h in set_cookie_headers if h.startswith("session=")), None
        )
        assert session_cookie is not None
        assert "HttpOnly" in session_cookie
        assert "SameSite=Lax" in session_cookie
        # TestingConfig define SESSION_COOKIE_SECURE=False
        assert "Secure" not in session_cookie
