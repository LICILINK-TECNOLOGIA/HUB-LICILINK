import sys
import importlib
from datetime import datetime, timedelta
from unittest.mock import patch

import click
import pytest

from app.extensions import db
from app.models.identity import User
from app.services.auth_service import AuthService

# Valores sintéticos, exclusivos desta suíte de testes.
SYNTHETIC_ADMIN_PASSWORD = "synthetic-strong-password-123"
SYNTHETIC_SHORT_PASSWORD = "short1"


def _password_input(password, confirm=None):
    return f"{password}\n{confirm if confirm is not None else password}\n"


@pytest.fixture
def cli_runner(app):
    return app.test_cli_runner()


class TestCliModuleImportHasNoSideEffects:
    def test_importing_cli_module_does_not_call_create_app(self):
        from unittest.mock import patch

        sys.modules.pop("app.cli", None)
        with patch("app.create_app") as mock_create_app:
            importlib.import_module("app.cli")
            mock_create_app.assert_not_called()

    def test_importing_cli_module_does_not_touch_database(self):
        # Fora de qualquer contexto de aplicação (nenhuma fixture `app`/`client`
        # usada neste teste): se o módulo tentasse consultar ou alterar o banco
        # no nível de módulo, esta importação já falharia com
        # "Working outside of application context". O import ser bem-sucedido
        # comprova que nenhum acesso ao banco ocorre apenas ao importar.
        sys.modules.pop("app.cli", None)
        importlib.import_module("app.cli")


class TestPasswordIsNeverACommandLineArgument:
    def test_create_admin_rejects_password_option(self, cli_runner):
        result = cli_runner.invoke(
            args=[
                "create-admin",
                "--name", "Admin Teste",
                "--email", "admin.naoaceita@example.com",
                "--password", "qualquer-coisa",
            ]
        )
        assert result.exit_code != 0
        assert "no such option" in result.output.lower()
        assert User.query.count() == 0

    def test_reset_admin_password_rejects_password_option(self, cli_runner):
        result = cli_runner.invoke(
            args=[
                "reset-admin-password",
                "--email", "admin.naoaceita@example.com",
                "--password", "qualquer-coisa",
                "--yes",
            ]
        )
        assert result.exit_code != 0
        assert "no such option" in result.output.lower()

    def test_password_prompt_is_hidden_with_confirmation(self, cli_runner, monkeypatch):
        captured_kwargs = {}

        def _fake_prompt(text, **kwargs):
            captured_kwargs["text"] = text
            captured_kwargs.update(kwargs)
            return SYNTHETIC_ADMIN_PASSWORD

        monkeypatch.setattr(click, "prompt", _fake_prompt)

        cli_runner.invoke(
            args=["create-admin", "--name", "Admin Oculto", "--email", "admin.oculto@example.com"]
        )

        assert captured_kwargs.get("hide_input") is True
        assert captured_kwargs.get("confirmation_prompt")


class TestCreateAdminCommand:
    def test_creation_requires_explicit_name_and_email(self, cli_runner):
        result = cli_runner.invoke(args=["create-admin"])
        assert result.exit_code != 0
        assert User.query.count() == 0

    def test_no_default_password_exists(self, cli_runner):
        # Sem nenhuma entrada interativa disponível, o prompt de senha deve
        # falhar (EOF) em vez de assumir qualquer valor padrão.
        result = cli_runner.invoke(
            args=["create-admin", "--name", "Admin Teste", "--email", "admin.semdefault@example.com"],
            input="",
        )
        assert result.exit_code != 0
        assert User.query.count() == 0

    def test_mismatched_password_confirmation_is_rejected(self, cli_runner):
        result = cli_runner.invoke(
            args=["create-admin", "--name", "Admin Mismatch", "--email", "admin.mismatch@example.com"],
            input=_password_input("senha-um-sintetica-123", confirm="senha-dois-sintetica-456"),
        )
        assert result.exit_code != 0
        assert User.query.filter_by(email="admin.mismatch@example.com").count() == 0

    def test_no_interactive_input_fails_without_db_change(self, cli_runner):
        result = cli_runner.invoke(
            args=["create-admin", "--name", "Admin SemInput", "--email", "admin.seminput@example.com"],
            input="",
        )
        assert result.exit_code != 0
        assert User.query.filter_by(email="admin.seminput@example.com").count() == 0

    def test_password_never_appears_in_output(self, cli_runner):
        result = cli_runner.invoke(
            args=["create-admin", "--name", "Admin Teste", "--email", "admin.saida@example.com"],
            input=_password_input(SYNTHETIC_ADMIN_PASSWORD),
        )
        assert SYNTHETIC_ADMIN_PASSWORD not in result.output

    def test_password_never_appears_in_error_message(self, cli_runner):
        result = cli_runner.invoke(
            args=["create-admin", "--name", "Admin Teste", "--email", "admin.curto@example.com"],
            input=_password_input(SYNTHETIC_SHORT_PASSWORD),
        )
        assert result.exit_code != 0
        assert SYNTHETIC_SHORT_PASSWORD not in result.output
        assert User.query.count() == 0

    def test_password_never_appears_in_exception(self, cli_runner, monkeypatch):
        def _raise_commit(*args, **kwargs):
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(db.session, "commit", _raise_commit)

        result = cli_runner.invoke(
            args=["create-admin", "--name", "Admin Falha", "--email", "admin.falhaexc@example.com"],
            input=_password_input(SYNTHETIC_ADMIN_PASSWORD),
        )

        assert SYNTHETIC_ADMIN_PASSWORD not in result.output
        if result.exception:
            assert SYNTHETIC_ADMIN_PASSWORD not in str(result.exception)

    def test_password_is_stored_only_as_hash(self, cli_runner):
        cli_runner.invoke(
            args=["create-admin", "--name", "Admin Teste", "--email", "admin.hash@example.com"],
            input=_password_input(SYNTHETIC_ADMIN_PASSWORD),
        )
        admin = User.query.filter_by(email="admin.hash@example.com").first()
        assert admin is not None
        assert admin.password_hash != SYNTHETIC_ADMIN_PASSWORD
        assert admin.check_password(SYNTHETIC_ADMIN_PASSWORD) is True

    def test_existing_admin_is_not_silently_modified(self, cli_runner):
        email = "admin.existente@example.com"
        cli_runner.invoke(
            args=["create-admin", "--name", "Admin Um", "--email", email],
            input=_password_input(SYNTHETIC_ADMIN_PASSWORD),
        )
        admin = User.query.filter_by(email=email).first()
        original_hash = admin.password_hash

        # E-mail já existe: nem chega a solicitar senha, nenhuma entrada necessária.
        result = cli_runner.invoke(
            args=["create-admin", "--name", "Admin Dois", "--email", email], input=""
        )

        db.session.refresh(admin)
        assert admin.password_hash == original_hash
        assert "já está em uso" in result.output

    def test_duplicate_creation_is_rejected(self, cli_runner):
        email = "admin.duplicado@example.com"
        cli_runner.invoke(
            args=["create-admin", "--name", "Admin Um", "--email", email],
            input=_password_input(SYNTHETIC_ADMIN_PASSWORD),
        )
        cli_runner.invoke(args=["create-admin", "--name", "Admin Dois", "--email", email], input="")

        assert User.query.filter_by(email=email).count() == 1

    def test_invalid_password_leaves_no_partial_state(self, cli_runner):
        before = User.query.count()
        cli_runner.invoke(
            args=["create-admin", "--name", "Admin Invalido", "--email", "admin.invalido@example.com"],
            input=_password_input(SYNTHETIC_SHORT_PASSWORD),
        )
        assert User.query.count() == before

    def test_creation_rolls_back_on_commit_failure(self, cli_runner, monkeypatch):
        def _raise_commit(*args, **kwargs):
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(db.session, "commit", _raise_commit)

        result = cli_runner.invoke(
            args=["create-admin", "--name", "Admin Rollback", "--email", "admin.rollback@example.com"],
            input=_password_input(SYNTHETIC_ADMIN_PASSWORD),
        )

        assert result.exit_code != 0
        assert User.query.filter_by(email="admin.rollback@example.com").count() == 0

    def test_password_hash_never_appears_in_output(self, cli_runner):
        result = cli_runner.invoke(
            args=["create-admin", "--name", "Admin Hash Saida", "--email", "admin.hashsaida@example.com"],
            input=_password_input(SYNTHETIC_ADMIN_PASSWORD),
        )
        admin = User.query.filter_by(email="admin.hashsaida@example.com").first()
        assert admin is not None
        assert admin.password_hash not in result.output

    def test_successful_creation_results_in_exactly_one_user(self, cli_runner):
        assert User.query.count() == 0
        cli_runner.invoke(
            args=["create-admin", "--name", "Admin Unico", "--email", "admin.unico@example.com"],
            input=_password_input(SYNTHETIC_ADMIN_PASSWORD),
        )
        assert User.query.count() == 1


class TestCreateAdminSetsVerifiedEmail:
    """Issue #17: regressão do bug em que create_admin_command criava o
    administrador com email_verified_at=None, impedindo login imediato
    ("E-mail não verificado" em AuthService.authenticate)."""

    def test_created_admin_has_email_verified_at_set(self, cli_runner):
        before = datetime.utcnow()
        cli_runner.invoke(
            args=["create-admin", "--name", "Admin Verificado", "--email", "admin.verificado@example.com"],
            input=_password_input(SYNTHETIC_ADMIN_PASSWORD),
        )
        after = datetime.utcnow()

        admin = User.query.filter_by(email="admin.verificado@example.com").first()
        assert admin is not None
        assert admin.email_verified_at is not None
        # Confere que a data ficou dentro da janela da execução do teste
        # (folga de 1s para variação de precisão), usando a mesma convenção
        # de UTC ingênuo (datetime.utcnow()) já usada em
        # AuthService.verify_email() e nos demais models do projeto.
        assert before - timedelta(seconds=1) <= admin.email_verified_at <= after + timedelta(seconds=1)

    def test_created_admin_remains_active_and_internal_admin(self, cli_runner):
        cli_runner.invoke(
            args=["create-admin", "--name", "Admin Flags", "--email", "admin.flags@example.com"],
            input=_password_input(SYNTHETIC_ADMIN_PASSWORD),
        )

        admin = User.query.filter_by(email="admin.flags@example.com").first()
        assert admin is not None
        assert admin.is_active is True
        assert admin.is_internal_admin is True

    def test_created_admin_can_authenticate_immediately(self, cli_runner):
        cli_runner.invoke(
            args=["create-admin", "--name", "Admin Login Imediato", "--email", "admin.loginimediato@example.com"],
            input=_password_input(SYNTHETIC_ADMIN_PASSWORD),
        )

        authenticated_user = AuthService.authenticate(
            "admin.loginimediato@example.com", SYNTHETIC_ADMIN_PASSWORD
        )
        assert authenticated_user is not None
        assert authenticated_user.email == "admin.loginimediato@example.com"

    @patch("app.services.auth_service.EmailService.send_verification_email")
    def test_regular_user_registration_still_requires_verification(self, mock_send, app):
        # Confirma que a correção do CLI (que passou a preencher
        # email_verified_at em create-admin) não afeta o fluxo público real
        # de autorregistro: exercitamos AuthService.start_registration/
        # verify_email diretamente (não construímos User manualmente), e o
        # envio de e-mail é mockado para não disparar nenhum e-mail externo.
        with app.app_context():
            mock_send.return_value = None
            email = "usuario.naoverificado@example.com"

            pending = AuthService.start_registration(
                "Usuario Comum Registrado", email, SYNTHETIC_ADMIN_PASSWORD
            )

            # Antes da verificação, nenhum User existe ainda (só a pendência)
            # e o login correspondente não é possível.
            assert User.query.filter_by(email=email).first() is None
            assert AuthService.authenticate(email, SYNTHETIC_ADMIN_PASSWORD) is None

            code = mock_send.call_args[1]["code"]
            verified_user = AuthService.verify_email(pending.id, code)

            # Após a verificação real (mesmo fluxo público de autorregistro),
            # o login passa a funcionar normalmente.
            assert verified_user.email_verified_at is not None
            authenticated_user = AuthService.authenticate(email, SYNTHETIC_ADMIN_PASSWORD)
            assert authenticated_user is not None
            assert authenticated_user.email == email


class TestResetAdminPasswordCommand:
    def _create_admin(self, cli_runner, email, password=SYNTHETIC_ADMIN_PASSWORD):
        cli_runner.invoke(
            args=["create-admin", "--name", "Admin Reset", "--email", email],
            input=_password_input(password),
        )

    def test_reset_requires_explicit_confirmation(self, cli_runner):
        email = "admin.semconfirmacao@example.com"
        self._create_admin(cli_runner, email)
        admin = User.query.filter_by(email=email).first()
        original_hash = admin.password_hash

        # Recusa a confirmação da ação ("n"): não deve nem chegar a pedir senha.
        result = cli_runner.invoke(
            args=["reset-admin-password", "--email", email], input="n\n"
        )

        db.session.refresh(admin)
        assert admin.password_hash == original_hash
        assert result.exit_code != 0

    def test_reset_with_explicit_confirmation_succeeds(self, cli_runner):
        email = "admin.comconfirmacao@example.com"
        self._create_admin(cli_runner, email)
        new_password = "nova-senha-sintetica-789"

        result = cli_runner.invoke(
            args=["reset-admin-password", "--email", email, "--yes"],
            input=_password_input(new_password),
        )

        admin = User.query.filter_by(email=email).first()
        assert admin.check_password(new_password) is True
        assert new_password not in result.output

    def test_reset_never_creates_new_admin(self, cli_runner):
        email = "nao.existe@example.com"
        before = User.query.count()

        result = cli_runner.invoke(
            args=["reset-admin-password", "--email", email, "--yes"], input=""
        )

        assert User.query.count() == before
        assert "nenhum administrador interno encontrado" in result.output.lower()

    def test_reset_password_never_appears_in_output(self, cli_runner):
        email = "admin.resetsaida@example.com"
        self._create_admin(cli_runner, email)
        new_password = "outra-senha-sintetica-reset"

        result = cli_runner.invoke(
            args=["reset-admin-password", "--email", email, "--yes"],
            input=_password_input(new_password),
        )
        assert new_password not in result.output

    def test_reset_rolls_back_on_commit_failure(self, cli_runner, monkeypatch):
        email = "admin.resetrollback@example.com"
        self._create_admin(cli_runner, email)
        admin = User.query.filter_by(email=email).first()
        original_hash = admin.password_hash

        def _raise_commit(*args, **kwargs):
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(db.session, "commit", _raise_commit)

        result = cli_runner.invoke(
            args=["reset-admin-password", "--email", email, "--yes"],
            input=_password_input("nova-senha-sintetica-rollback"),
        )

        db.session.refresh(admin)
        assert result.exit_code != 0
        assert admin.password_hash == original_hash


class TestSetPasswordAndCheckPasswordWorkTogether:
    def test_set_password_then_check_password_roundtrip(self, app):
        with app.app_context():
            user = User(name="Modelo Teste", email="modelo.teste@example.com", is_internal_admin=False)
            user.set_password(SYNTHETIC_ADMIN_PASSWORD)

            assert user.password_hash != SYNTHETIC_ADMIN_PASSWORD
            assert user.check_password(SYNTHETIC_ADMIN_PASSWORD) is True
            assert user.check_password("valor-sintetico-errado") is False


class TestEnvironmentValidationsUnaffected:
    def test_production_still_requires_secret_key(self, monkeypatch):
        from app import create_app

        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError):
            create_app("production")
