import sys
import importlib
from unittest.mock import patch, MagicMock

import pytest


def _import_run_fresh():
    # Garante execução real do corpo do módulo (não uma versão cacheada de
    # uma importação anterior), para que os testes de efeito colateral de
    # importação sejam significativos independentemente da ordem de execução.
    sys.modules.pop("run", None)
    return importlib.import_module("run")


@pytest.fixture
def run_module():
    module = _import_run_fresh()
    yield module
    sys.modules.pop("run", None)


class TestRunPyImportHasNoSideEffects:
    def test_importing_run_does_not_call_create_app(self):
        with patch("app.create_app") as mock_create_app:
            _import_run_fresh()
            mock_create_app.assert_not_called()
        sys.modules.pop("run", None)

    def test_importing_run_does_not_call_flask_run(self):
        with patch("flask.Flask.run") as mock_flask_run:
            _import_run_fresh()
            mock_flask_run.assert_not_called()
        sys.modules.pop("run", None)


class TestRunPyMain:
    def _fake_app(self, debug):
        fake_app = MagicMock()
        fake_app.debug = debug
        return fake_app

    def test_main_creates_development_app_explicitly(self, run_module, monkeypatch):
        monkeypatch.delenv("HUB_DEV_HOST", raising=False)
        monkeypatch.delenv("HUB_DEV_PORT", raising=False)
        fake_app = self._fake_app(debug=True)

        with patch.object(run_module, "create_app", return_value=fake_app) as mock_create_app:
            run_module.main()
            mock_create_app.assert_called_once_with("development")

    def test_main_passes_through_app_debug(self, run_module, monkeypatch):
        monkeypatch.delenv("HUB_DEV_HOST", raising=False)
        monkeypatch.delenv("HUB_DEV_PORT", raising=False)
        fake_app = self._fake_app(debug=False)

        with patch.object(run_module, "create_app", return_value=fake_app):
            run_module.main()

        fake_app.run.assert_called_once()
        _, kwargs = fake_app.run.call_args
        assert kwargs["debug"] is False

    def test_default_host_is_loopback(self, run_module, monkeypatch):
        monkeypatch.delenv("HUB_DEV_HOST", raising=False)
        monkeypatch.delenv("HUB_DEV_PORT", raising=False)
        fake_app = self._fake_app(debug=True)

        with patch.object(run_module, "create_app", return_value=fake_app):
            run_module.main()

        _, kwargs = fake_app.run.call_args
        assert kwargs["host"] == "127.0.0.1"

    def test_default_port_is_8000(self, run_module, monkeypatch):
        monkeypatch.delenv("HUB_DEV_HOST", raising=False)
        monkeypatch.delenv("HUB_DEV_PORT", raising=False)
        fake_app = self._fake_app(debug=True)

        with patch.object(run_module, "create_app", return_value=fake_app):
            run_module.main()

        _, kwargs = fake_app.run.call_args
        assert kwargs["port"] == 8000

    def test_hub_dev_host_and_port_env_vars_are_respected(self, run_module, monkeypatch):
        # Valores sintéticos, apenas para provar que as variáveis são lidas;
        # nenhum socket real é aberto (app.run está mockado).
        monkeypatch.setenv("HUB_DEV_HOST", "10.0.0.5")
        monkeypatch.setenv("HUB_DEV_PORT", "9001")
        fake_app = self._fake_app(debug=True)

        with patch.object(run_module, "create_app", return_value=fake_app):
            run_module.main()

        _, kwargs = fake_app.run.call_args
        assert kwargs["host"] == "10.0.0.5"
        assert kwargs["port"] == 9001

    def test_default_host_is_never_all_interfaces(self, run_module, monkeypatch):
        monkeypatch.delenv("HUB_DEV_HOST", raising=False)
        monkeypatch.delenv("HUB_DEV_PORT", raising=False)
        fake_app = self._fake_app(debug=True)

        with patch.object(run_module, "create_app", return_value=fake_app):
            run_module.main()

        _, kwargs = fake_app.run.call_args
        assert kwargs["host"] != "0.0.0.0"

    def test_run_never_forces_use_reloader(self, run_module, monkeypatch):
        monkeypatch.delenv("HUB_DEV_HOST", raising=False)
        monkeypatch.delenv("HUB_DEV_PORT", raising=False)
        fake_app = self._fake_app(debug=True)

        with patch.object(run_module, "create_app", return_value=fake_app):
            run_module.main()

        _, kwargs = fake_app.run.call_args
        assert "use_reloader" not in kwargs

    @pytest.mark.parametrize("invalid_port", ["not-a-number", "0", "65536", "-1"])
    def test_invalid_port_is_rejected(self, run_module, monkeypatch, invalid_port):
        monkeypatch.delenv("HUB_DEV_HOST", raising=False)
        monkeypatch.setenv("HUB_DEV_PORT", invalid_port)
        fake_app = self._fake_app(debug=True)

        with patch.object(run_module, "create_app", return_value=fake_app):
            with pytest.raises(RuntimeError):
                run_module.main()

        fake_app.run.assert_not_called()
