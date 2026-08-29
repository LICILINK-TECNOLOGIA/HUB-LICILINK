import pytest
from flask import Flask

from app import create_app
from app.config import (
    DevelopmentConfig,
    TestingConfig,
    StagingConfig,
    ProductionConfig,
    POSTGRES_DIALECT_PREFIX,
    DEFAULT_DEVELOPMENT_DATABASE_URI,
    configure_database_uri,
    _normalize_postgres_dialect,
)

# Valor sintético de SECRET_KEY, exclusivo desta suíte, apenas para permitir
# que create_app('production'/'staging') passe da validação de SECRET_KEY
# e chegue até a resolução de SQLALCHEMY_DATABASE_URI sob teste.
SYNTHETIC_SECRET_KEY = "q" * 32


def _clear_database_url_env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)


def _bare_app_with_config(config_class):
    app = Flask(__name__)
    app.config.from_object(config_class)
    return app


class TestPostgresDialectNormalization:
    def test_default_development_uri_uses_psycopg_dialect(self):
        assert DEFAULT_DEVELOPMENT_DATABASE_URI.startswith(POSTGRES_DIALECT_PREFIX)

    def test_no_resolved_uri_ever_requires_psycopg2(self, monkeypatch):
        _clear_database_url_env(monkeypatch)
        app = _bare_app_with_config(DevelopmentConfig)
        configure_database_uri(app)
        assert "psycopg2" not in app.config["SQLALCHEMY_DATABASE_URI"]

    def test_bare_postgres_scheme_is_normalized(self):
        normalized = _normalize_postgres_dialect(
            "postgres://synthetic_user:synthetic_pass@synthetic-host:5432/synthetic_db"
        )
        assert normalized == (
            "postgresql+psycopg://synthetic_user:synthetic_pass@synthetic-host:5432/synthetic_db"
        )

    def test_bare_postgresql_scheme_is_normalized(self):
        normalized = _normalize_postgres_dialect(
            "postgresql://synthetic_user:synthetic_pass@synthetic-host:5432/synthetic_db"
        )
        assert normalized == (
            "postgresql+psycopg://synthetic_user:synthetic_pass@synthetic-host:5432/synthetic_db"
        )

    def test_explicit_installed_driver_is_preserved_unchanged(self):
        # postgresql+psycopg:// já é o único driver instalado: preservada.
        explicit_url = "postgresql+psycopg://synthetic_user:synthetic_pass@synthetic-host/synthetic_db"
        assert _normalize_postgres_dialect(explicit_url) == explicit_url

    def test_explicit_psycopg2_dialect_is_rejected(self):
        explicit_url = "postgresql+psycopg2://synthetic_user:synthetic_pass@synthetic-host/synthetic_db"
        with pytest.raises(RuntimeError):
            _normalize_postgres_dialect(explicit_url)

    def test_other_unsupported_postgres_driver_is_rejected(self):
        # Qualquer dialeto PostgreSQL explícito que não seja o driver instalado
        # deve ser rejeitado, sem que o SQLAlchemy chegue a tentar importá-lo.
        explicit_url = "postgresql+asyncpg://synthetic_user:synthetic_pass@synthetic-host/synthetic_db"
        with pytest.raises(RuntimeError):
            _normalize_postgres_dialect(explicit_url)

    def test_rejection_error_message_never_reproduces_credentials(self):
        explicit_url = "postgresql+psycopg2://synthetic_user:synthetic_pass_value@synthetic-host:5432/synthetic_db"
        with pytest.raises(RuntimeError) as exc_info:
            _normalize_postgres_dialect(explicit_url)
        message = str(exc_info.value)
        assert "synthetic_user" not in message
        assert "synthetic_pass_value" not in message
        assert "synthetic-host" not in message
        assert "synthetic_db" not in message
        # A mensagem pode citar o nome do dialeto rejeitado (não é credencial).
        assert "psycopg2" in message

    def test_non_postgres_uri_is_untouched(self):
        assert _normalize_postgres_dialect("sqlite:///:memory:") == "sqlite:///:memory:"

    def test_empty_or_none_uri_is_untouched(self):
        assert _normalize_postgres_dialect(None) is None
        assert _normalize_postgres_dialect("") == ""


class TestDatabaseUrlRespectedAndDynamic:
    def test_external_database_url_with_bare_scheme_is_respected_and_normalized(self, monkeypatch):
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql://synthetic_user:synthetic_pass@synthetic-host:5432/synthetic_db",
        )
        app = _bare_app_with_config(ProductionConfig)
        configure_database_uri(app)
        assert app.config["SQLALCHEMY_DATABASE_URI"] == (
            "postgresql+psycopg://synthetic_user:synthetic_pass@synthetic-host:5432/synthetic_db"
        )

    def test_configure_database_uri_reads_current_env_value_not_import_time_value(self, monkeypatch):
        # Prova de regressão: configure_database_uri() precisa ler os.getenv
        # no momento da chamada, e não um valor congelado na importação de
        # app.config (o problema original relatado na Issue #7).
        monkeypatch.setenv(
            "DATABASE_URL", "postgresql://synthetic_user:synthetic_pass@host-um:5432/db_um"
        )
        app_1 = _bare_app_with_config(ProductionConfig)
        configure_database_uri(app_1)
        assert "host-um" in app_1.config["SQLALCHEMY_DATABASE_URI"]

        monkeypatch.setenv(
            "DATABASE_URL", "postgresql://synthetic_user:synthetic_pass@host-dois:5432/db_dois"
        )
        app_2 = _bare_app_with_config(ProductionConfig)
        configure_database_uri(app_2)
        assert "host-dois" in app_2.config["SQLALCHEMY_DATABASE_URI"]
        assert app_1.config["SQLALCHEMY_DATABASE_URI"] != app_2.config["SQLALCHEMY_DATABASE_URI"]

    def test_development_uses_local_default_when_database_url_absent(self, monkeypatch):
        _clear_database_url_env(monkeypatch)
        app = _bare_app_with_config(DevelopmentConfig)
        configure_database_uri(app)
        assert app.config["SQLALCHEMY_DATABASE_URI"] == DEFAULT_DEVELOPMENT_DATABASE_URI

    def test_production_without_database_url_fails_fast_and_never_defaults_locally(self, monkeypatch):
        # Produção nunca deve herdar o padrão local de desenvolvimento; deve
        # falhar de forma clara e antecipada em vez de seguir com URI vazia.
        _clear_database_url_env(monkeypatch)
        app = _bare_app_with_config(ProductionConfig)
        with pytest.raises(RuntimeError):
            configure_database_uri(app)

    def test_staging_without_database_url_fails_fast(self, monkeypatch):
        _clear_database_url_env(monkeypatch)
        app = _bare_app_with_config(StagingConfig)
        with pytest.raises(RuntimeError):
            configure_database_uri(app)


class TestTestingConfigUsesSQLite:
    def test_testing_config_uri_is_sqlite(self):
        assert TestingConfig.SQLALCHEMY_DATABASE_URI == "sqlite:///:memory:"

    def test_configure_database_uri_does_not_override_existing_sqlite_uri(self, monkeypatch):
        # Mesmo com DATABASE_URL setada no ambiente, uma config que já define
        # sua própria URI (como TestingConfig) não deve ser sobrescrita.
        monkeypatch.setenv(
            "DATABASE_URL", "postgresql://synthetic_user:synthetic_pass@synthetic-host/synthetic_db"
        )
        app = _bare_app_with_config(TestingConfig)
        configure_database_uri(app)
        assert app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite:///:memory:"


class TestDefaultDevelopmentUriHasNoNewSecret:
    def test_default_development_uri_reuses_known_local_dev_credentials(self):
        # As credenciais no padrão local de development são as mesmas já
        # versionadas em docker-compose.dev.yml (hub_user/hub_password),
        # não um segredo novo introduzido por esta Issue.
        assert "hub_user:hub_password" in DEFAULT_DEVELOPMENT_DATABASE_URI


class TestFactoryResolvesDatabaseUriPredictably:
    def test_create_app_production_normalizes_bare_postgres_url_end_to_end(self, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", SYNTHETIC_SECRET_KEY)
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql://synthetic_user:synthetic_pass@localhost:59999/synthetic_db",
        )
        app = create_app("production")
        assert app.config["SQLALCHEMY_DATABASE_URI"].startswith(POSTGRES_DIALECT_PREFIX)
        assert "psycopg2" not in app.config["SQLALCHEMY_DATABASE_URI"]

    def test_create_app_development_works_without_external_database_url(self, monkeypatch):
        monkeypatch.delenv("SECRET_KEY", raising=False)
        _clear_database_url_env(monkeypatch)
        app = create_app("development")
        assert app.config["SQLALCHEMY_DATABASE_URI"] == DEFAULT_DEVELOPMENT_DATABASE_URI

    def test_create_app_testing_still_uses_sqlite(self):
        app = create_app("testing")
        assert app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite:///:memory:"

    def test_create_app_production_without_database_url_fails_before_sqlalchemy_init(
        self, monkeypatch
    ):
        monkeypatch.setenv("SECRET_KEY", SYNTHETIC_SECRET_KEY)
        _clear_database_url_env(monkeypatch)

        from app.extensions import db

        def _fail_if_called(*args, **kwargs):
            raise AssertionError(
                "db.init_app não deveria ser chamado quando DATABASE_URL está ausente em produção"
            )

        monkeypatch.setattr(db, "init_app", _fail_if_called)

        with pytest.raises(RuntimeError):
            create_app("production")

    def test_create_app_staging_without_database_url_fails_before_sqlalchemy_init(
        self, monkeypatch
    ):
        monkeypatch.setenv("SECRET_KEY", SYNTHETIC_SECRET_KEY)
        _clear_database_url_env(monkeypatch)

        from app.extensions import db

        def _fail_if_called(*args, **kwargs):
            raise AssertionError(
                "db.init_app não deveria ser chamado quando DATABASE_URL está ausente em staging"
            )

        monkeypatch.setattr(db, "init_app", _fail_if_called)

        with pytest.raises(RuntimeError):
            create_app("staging")
