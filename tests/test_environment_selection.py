import pytest

from app import create_app
from app.config import (
    resolve_config_name,
    config_by_name,
    DevelopmentConfig,
    TestingConfig,
    StagingConfig,
    ProductionConfig,
)

# Valores sintéticos, exclusivos desta suíte, apenas para permitir que
# create_app('staging'/'production') passe das validações já existentes de
# SECRET_KEY/DATABASE_URL (Issues #5 e #7) e confirme que a seleção de
# ambiente continua funcionando de ponta a ponta.
SYNTHETIC_SECRET_KEY = "e" * 32
SYNTHETIC_DATABASE_URL = (
    "postgresql+psycopg://synthetic_user:synthetic_pass@localhost:59999/synthetic_db"
)


def _clear_flask_env(monkeypatch):
    monkeypatch.delenv("FLASK_ENV", raising=False)


class TestResolveConfigNameExplicit:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("development", "development"),
            ("testing", "testing"),
            ("staging", "staging"),
            ("production", "production"),
            ("PRODUCTION", "production"),
            ("  staging  ", "staging"),
            ("Testing", "testing"),
        ],
    )
    def test_explicit_valid_name_is_accepted_and_normalized(self, name, expected):
        assert resolve_config_name(name) == expected

    def test_explicit_empty_string_is_rejected(self):
        with pytest.raises(RuntimeError):
            resolve_config_name("")

    def test_explicit_whitespace_only_is_rejected(self):
        with pytest.raises(RuntimeError):
            resolve_config_name("   ")

    def test_explicit_unknown_name_is_rejected(self):
        with pytest.raises(RuntimeError):
            resolve_config_name("nao-existe")

    def test_error_message_lists_only_allowed_environment_names(self):
        with pytest.raises(RuntimeError) as exc_info:
            resolve_config_name("nao-existe")
        message = str(exc_info.value)
        for name in config_by_name.keys():
            assert name in message
        # Não ecoa o valor inválido recebido nem nenhuma variável sensível.
        assert "nao-existe" not in message


class TestResolveConfigNameFromFlaskEnv:
    def test_valid_flask_env_is_accepted_when_config_name_not_given(self, monkeypatch):
        monkeypatch.setenv("FLASK_ENV", "staging")
        assert resolve_config_name(None) == "staging"

    def test_flask_env_is_normalized(self, monkeypatch):
        monkeypatch.setenv("FLASK_ENV", "  PRODUCTION  ")
        assert resolve_config_name(None) == "production"

    def test_absence_of_both_results_in_development(self, monkeypatch):
        _clear_flask_env(monkeypatch)
        assert resolve_config_name(None) == "development"

    def test_explicit_empty_flask_env_is_rejected_not_defaulted_to_development(self, monkeypatch):
        # FLASK_ENV="" é diferente de FLASK_ENV ausente: não deve cair
        # silenciosamente em development.
        monkeypatch.setenv("FLASK_ENV", "")
        with pytest.raises(RuntimeError):
            resolve_config_name(None)

    def test_invalid_flask_env_is_rejected(self, monkeypatch):
        monkeypatch.setenv("FLASK_ENV", "nao-existe")
        with pytest.raises(RuntimeError):
            resolve_config_name(None)


class TestDebugReflectsConfigClass:
    def test_debug_flags_per_class(self):
        assert DevelopmentConfig.DEBUG is True
        assert StagingConfig.DEBUG is False
        assert ProductionConfig.DEBUG is False
        # TestingConfig não define DEBUG explicitamente; Flask usa False por padrão.
        assert getattr(TestingConfig, "DEBUG", False) is False


class TestCreateAppUsesResolveConfigName:
    def test_create_app_invalid_config_name_raises(self):
        with pytest.raises(RuntimeError):
            create_app("nao-existe")

    def test_create_app_empty_config_name_raises(self):
        with pytest.raises(RuntimeError):
            create_app("")

    def test_create_app_testing_still_works_explicitly(self):
        app = create_app("testing")
        assert app.config["TESTING"] is True
        assert app.config["DEBUG"] is False

    def test_create_app_staging_and_production_still_work_explicitly(self, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", SYNTHETIC_SECRET_KEY)
        monkeypatch.setenv("DATABASE_URL", SYNTHETIC_DATABASE_URL)

        staging_app = create_app("staging")
        assert staging_app.config["IS_PRODUCTION"] is True
        assert staging_app.config["DEBUG"] is False

        production_app = create_app("production")
        assert production_app.config["IS_PRODUCTION"] is True
        assert production_app.config["DEBUG"] is False

    def test_invalid_config_name_never_falls_back_to_development(self):
        # Uma seleção inválida não deve, em nenhuma hipótese, resultar
        # silenciosamente em DevelopmentConfig.
        try:
            create_app("ambiente-desconhecido")
        except RuntimeError:
            pass
        else:
            pytest.fail("create_app deveria ter levantado RuntimeError")
