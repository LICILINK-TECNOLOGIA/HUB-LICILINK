"""Issue #69: regressão para o `user_loader` registrado em `app/__init__.py`.

Antes da correção, `models.User.query.get(user_id)` recebia sempre uma
`str` (Flask-Login nunca entrega outro tipo - `UserMixin.get_id()` faz
`str(self.id)`), mas a PK real é `UUID(as_uuid=True)`. No SQLite (sem
suporte nativo a UUID), o bind processor exige um `uuid.UUID` de verdade e
quebra com `AttributeError: 'str' object has no attribute 'hex'`.

O fixture `app` de `tests/conftest.py` mantém um único `app.app_context()`
aberto durante todo o corpo do teste. O `RequestContext.push()` do Flask
reaproveita esse app-context em vez de empurrar um novo por requisição do
`test_client`, o que mantém a mesma `db.session`/identity map viva entre a
requisição de login e a seguinte - por isso um teste de login em duas
requisições usando aquele fixture nunca chega a emitir a query SQL que
aciona o bind processor com defeito (falso-negativo). Por isso este
arquivo usa um fixture próprio (`isolated_app`/`isolated_client`) que sai
completamente do app-context antes de qualquer requisição HTTP, garantindo
uma `db.session` nova por requisição - o mesmo que o servidor real via
`run.py` faz.
"""
import re
import uuid
from datetime import datetime, timezone

import pytest
from flask import has_app_context
from sqlalchemy import event

SYNTHETIC_PASSWORD = "senha-sintetica-issue-69-123"
CSRF_RE = re.compile(r'name="csrf_token"\s+value="([^"]+)"')


def _extract_csrf(client, url="/login"):
    resp = client.get(url)
    match = CSRF_RE.search(resp.get_data(as_text=True))
    assert match, f"csrf_token não encontrado em {url!r} (status {resp.status_code})"
    return match.group(1)


def _login(client, email, password):
    token = _extract_csrf(client, "/login")
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": token},
        follow_redirects=False,
    )


@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
    """Aplicação própria com SQLite em arquivo temporário sob `tmp_path`
    (nunca `:memory:`, nunca um dos bancos manuais protegidos). As tabelas
    são criadas dentro de um `app.app_context()` que é fechado
    explicitamente antes de qualquer requisição HTTP - ver docstring do
    módulo. `create_app('testing')` não serve aqui porque `TestingConfig`
    fixa `SQLALCHEMY_DATABASE_URI='sqlite:///:memory:'` e ignora
    `DATABASE_URL`; por isso usamos `create_app('development')` com
    `DATABASE_URL` definida via `monkeypatch.setenv` antes da chamada
    (única forma confirmada de direcionar o SQLAlchemy para o arquivo
    temporário, já que `configure_database_uri()` lê `os.getenv(
    'DATABASE_URL')` de forma síncrona dentro de `create_app()`, antes de
    `db.init_app()` resolver a URI - `monkeypatch.setenv` já mutou
    `os.environ` nesse ponto, e o pytest restaura a variável
    automaticamente ao final do teste, mesmo se uma asserção falhar)."""
    db_path = tmp_path / "issue69_isolated.db"
    db_uri = "sqlite:///" + str(db_path).replace("\\", "/")

    monkeypatch.setenv("DATABASE_URL", db_uri)

    from app import create_app

    app = create_app("development")

    app.config.update({
        "SECRET_KEY": "isolated-test-key-issue-69",
        "IS_PRODUCTION": False,
        "TESTING": True,
        "L_KALENDER_URL": "http://kalender.local",
        "L_GEDO_URL": "https://gedo.local/path",
    })

    from app.extensions import db
    from app import models

    with app.app_context():
        db.create_all()

        user = models.User(name="Usuario Issue 69", email="usuario.issue69@example.test", is_active=True)
        user.email_verified_at = datetime.now(timezone.utc)
        user.set_password(SYNTHETIC_PASSWORD)
        db.session.add(user)
        db.session.commit()

        user_id = user.id
        user_email = user.email
        db.session.remove()

    assert has_app_context() is False

    yield app, user_id, user_email

    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def isolated_client(isolated_app):
    app, user_id, user_email = isolated_app
    assert has_app_context() is False
    return app.test_client(), app, user_id, user_email


def _is_users_pk_lookup(statement):
    """`True` somente para um SELECT que busca na tabela `users` com
    predicado sobre `users.id` - não qualquer consulta que apenas mencione
    "users" e "where" em qualquer lugar. Normaliza caixa/quebras de
    linha/espaços repetidos (o compilador do SQLAlchemy formata a mesma
    consulta de forma diferente dependendo do dialeto/versão), sem se
    acoplar ao placeholder exato (`?`/`%s`/`:param`) nem à lista completa
    de colunas selecionadas."""
    normalized = " ".join(statement.lower().split())
    return "from users" in normalized and "where users.id =" in normalized


def _sql_listener(app):
    """Instala um listener real em `before_cursor_execute` no engine da
    app (não um monkeypatch em outro símbolo) e devolve `(remove, captured)`
    - `captured` acumula cada statement SQL executado até `remove()` ser
    chamado."""
    from app.extensions import db

    with app.app_context():
        engine = db.engine

    captured = []

    def _listener(conn, cursor, statement, parameters, context, executemany):
        captured.append(statement)

    event.listen(engine, "before_cursor_execute", _listener)

    def _remove():
        event.remove(engine, "before_cursor_execute", _listener)

    return _remove, captured


class TestUserLoaderHttpTwoRequestFlow:
    """Fluxo HTTP real de duas requisições - login e depois uma rota
    protegida - sem app-context persistente entre elas."""

    def test_login_then_protected_route_succeeds_without_500(self, isolated_client):
        client, app, user_id, user_email = isolated_client

        resp_login = _login(client, user_email, SYNTHETIC_PASSWORD)
        assert resp_login.status_code == 302

        with client.session_transaction() as sess:
            stored_user_id = sess.get("_user_id")
        assert isinstance(stored_user_id, str)
        assert stored_user_id == str(user_id)

        assert has_app_context() is False

        remove_listener, captured = _sql_listener(app)
        try:
            resp_protected = client.get("/", follow_redirects=False)
        finally:
            remove_listener()

        assert resp_protected.status_code == 200

        users_pk_queries = [s for s in captured if _is_users_pk_lookup(s)]
        assert users_pk_queries, (
            "esperava ao menos uma consulta real 'FROM users WHERE users.id = ...' na "
            "segunda requisição - se a lista estiver vazia, o resultado pode ter vindo "
            "de um cache/identity map da requisição anterior em vez de uma query nova"
        )

    def test_malformed_session_user_id_redirects_never_500(self, isolated_client):
        client, app, user_id, user_email = isolated_client

        resp_login = _login(client, user_email, SYNTHETIC_PASSWORD)
        assert resp_login.status_code == 302

        with client.session_transaction() as sess:
            sess["_user_id"] = "isto-nao-e-um-uuid-valido"

        resp = client.get("/", follow_redirects=False)
        assert resp.status_code in (302, 401)
        assert resp.status_code != 500

    def test_valid_but_nonexistent_uuid_in_session_redirects_never_500(self, isolated_client):
        client, app, user_id, user_email = isolated_client

        resp_login = _login(client, user_email, SYNTHETIC_PASSWORD)
        assert resp_login.status_code == 302

        with client.session_transaction() as sess:
            sess["_user_id"] = str(uuid.uuid4())

        resp = client.get("/", follow_redirects=False)
        assert resp.status_code in (302, 401)
        assert resp.status_code != 500

    def test_logout_then_protected_route_requires_login_again(self, isolated_client):
        client, app, user_id, user_email = isolated_client

        _login(client, user_email, SYNTHETIC_PASSWORD)
        assert client.get("/", follow_redirects=False).status_code == 200

        logout_token = _extract_csrf(client, "/login")
        resp_logout = client.post("/logout", data={"csrf_token": logout_token})
        assert resp_logout.status_code == 302

        resp_after_logout = client.get("/", follow_redirects=False)
        assert resp_after_logout.status_code == 302
        assert resp_after_logout.status_code != 500


class TestUserLoaderCallbackMatrixFindsUser:
    """Chama o callback real registrado (`app.login_manager._user_callback`)
    diretamente com entradas que devem localizar o usuário."""

    def test_uuid_instance(self, isolated_app):
        app, user_id, user_email = isolated_app
        from app.extensions import db

        with app.app_context():
            callback = app.login_manager._user_callback
            result = callback(user_id)
            assert result is not None
            assert result.id == user_id
            db.session.remove()

    def test_canonical_string(self, isolated_app):
        app, user_id, user_email = isolated_app
        from app.extensions import db

        with app.app_context():
            callback = app.login_manager._user_callback
            result = callback(str(user_id))
            assert result is not None
            assert result.id == user_id
            db.session.remove()

    def test_hex_string_without_hyphens(self, isolated_app):
        app, user_id, user_email = isolated_app
        from app.extensions import db

        with app.app_context():
            callback = app.login_manager._user_callback
            result = callback(user_id.hex)
            assert result is not None
            assert result.id == user_id
            db.session.remove()

    def test_uppercase_hex_string(self, isolated_app):
        app, user_id, user_email = isolated_app
        from app.extensions import db

        with app.app_context():
            callback = app.login_manager._user_callback
            result = callback(str(user_id).upper())
            assert result is not None
            assert result.id == user_id
            db.session.remove()


class TestUserLoaderCallbackMatrixReturnsNone:
    """Entradas que devem sempre devolver `None`, sem exceção."""

    class _RaisesOnStr:
        def __str__(self):
            raise RuntimeError("__str__ proposital para o teste")

    @pytest.mark.parametrize(
        "label, value_factory",
        [
            ("uuid valido inexistente", lambda: str(uuid.uuid4())),
            ("None", lambda: None),
            ("string vazia", lambda: ""),
            ("whitespace", lambda: "   "),
            ("uuid malformado", lambda: "nao-e-um-uuid"),
            ("inteiro pequeno", lambda: 42),
            ("inteiro de 32 digitos hexadecimal-compativel", lambda: 86991844722847430000000000000012),
            ("bool True", lambda: True),
            ("bool False", lambda: False),
            ("lista", lambda: [1, 2, 3]),
            ("dicionario", lambda: {"a": 1}),
            ("bytes", lambda: b"09b6e0b6a2ed4f0d8f2e0000000000000"),
            ("objeto comum", lambda: object()),
        ],
    )
    def test_returns_none_without_raising(self, isolated_app, label, value_factory):
        app, user_id, user_email = isolated_app
        from app.extensions import db

        with app.app_context():
            callback = app.login_manager._user_callback
            result = callback(value_factory())
            assert result is None, f"esperava None para entrada {label!r}"
            db.session.remove()

    def test_object_whose_str_raises_returns_none_without_propagating(self, isolated_app):
        app, user_id, user_email = isolated_app
        from app.extensions import db

        with app.app_context():
            callback = app.login_manager._user_callback
            result = callback(self._RaisesOnStr())
            assert result is None
            db.session.remove()

    def test_32_digit_int_would_parse_as_uuid_if_type_gate_were_absent(self, isolated_app):
        """Prova que o gate de tipo é necessário: a conversão isolada
        `uuid.UUID(str(valor))` para este inteiro específico NÃO levanta
        exceção (o texto é hexadecimal válido) - só o gate de tipo evita
        que ele seja aceito pelo callback."""
        value = 86991844722847430000000000000012
        normalized = uuid.UUID(str(value))
        assert isinstance(normalized, uuid.UUID)

        app, user_id, user_email = isolated_app
        from app.extensions import db

        with app.app_context():
            callback = app.login_manager._user_callback
            result = callback(value)
            assert result is None
            db.session.remove()


class TestUserLoaderNoQueryOnInvalidInput:
    """Confirma, com um listener SQL real, que nenhuma consulta a `users`
    é emitida quando a entrada é inválida - só depois de um controle
    positivo que prova que o listener realmente detecta uma consulta."""

    def test_no_sql_query_for_invalid_inputs(self, isolated_app):
        app, user_id, user_email = isolated_app

        remove_listener, captured = _sql_listener(app)
        try:
            from app.extensions import db

            # Controle positivo: entrada válida DEVE gerar consulta real.
            with app.app_context():
                callback = app.login_manager._user_callback
                result = callback(str(user_id))
                assert result is not None
                db.session.remove()

            positive_control_queries = [s for s in captured if "users" in s.lower()]
            assert positive_control_queries, (
                "controle positivo falhou: o listener não capturou nenhuma consulta a "
                "`users` para uma entrada válida - o restante deste teste não seria confiável"
            )

            captured.clear()

            invalid_inputs = [
                None, "", "   ", "nao-e-um-uuid", 42,
                86991844722847430000000000000012, True, False,
                [1, 2], {"a": 1}, object(),
            ]
            with app.app_context():
                callback = app.login_manager._user_callback
                for value in invalid_inputs:
                    result = callback(value)
                    assert result is None
                db.session.remove()

            users_queries_after_invalid = [s for s in captured if "users" in s.lower()]
            assert users_queries_after_invalid == [], (
                "nenhuma entrada invalida deveria emitir consulta a `users`, mas "
                f"capturei: {users_queries_after_invalid!r}"
            )
        finally:
            remove_listener()


class TestUserLoaderOperationalErrorPropagation:
    """M1 da revisão técnica: o `try/except Exception` do `user_loader`
    deve proteger somente a normalização do UUID (`uuid.UUID(str(...))`) -
    uma falha operacional real de `db.session.get()` (ex.: banco
    indisponível) precisa continuar se propagando como exceção, nunca ser
    engolida e convertida em `None`. Se fosse convertida, uma
    indisponibilidade real do banco seria indistinguível de uma sessão
    inválida (o usuário simplesmente perderia a sessão silenciosamente,
    mascarando uma falha de infraestrutura)."""

    def test_operational_error_from_session_get_is_not_swallowed(self, isolated_app, monkeypatch):
        import sqlalchemy.exc

        app, user_id, user_email = isolated_app
        from app.extensions import db

        # Mesmo padrão já estabelecido em
        # tests/test_organization_product_installation_service.py
        # (`test_integrity_error_during_creation_is_operational_and_rolls_back`)
        # para simular uma falha operacional sintética do SQLAlchemy:
        # `OperationalError` é uma subclasse concreta de `SQLAlchemyError`,
        # categoria real de falha de banco (ex.: conexão perdida) -
        # distinta de qualquer erro de conversão de UUID, que é o único
        # caso que o `except Exception` da normalização deve capturar.
        def _raise_operational_error(model, ident, *args, **kwargs):
            raise sqlalchemy.exc.OperationalError(
                "SELECT users.* FROM users WHERE users.id = ?",
                {},
                Exception("falha sintética de conexão - Issue #69 M1"),
            )

        with app.app_context():
            monkeypatch.setattr(db.session, "get", _raise_operational_error)
            callback = app.login_manager._user_callback

            with pytest.raises(sqlalchemy.exc.OperationalError) as exc_info:
                callback(str(user_id))

            # A exceção propagada nunca deve conter o identificador bruto
            # (o `user_loader` não o registra nem o interpola em nenhuma
            # mensagem própria - qualquer texto de erro vem inteiramente
            # do SQLAlchemy/da exceção sintética acima, não do callback).
            assert str(user_id) not in str(exc_info.value)

            db.session.remove()


class TestUserLoaderPostgresCompatibility:
    """Sem conectar a um PostgreSQL real: confirma estruturalmente que o
    argumento entregue a `db.session.get` é sempre `uuid.UUID`, o que é
    compatível tanto com o bind processor do SQLite (exige `uuid.UUID`)
    quanto com o caminho nativo do PostgreSQL (aceita `uuid.UUID`
    diretamente, sem exigir string)."""

    def test_session_get_always_receives_uuid_instance(self, isolated_app, monkeypatch):
        app, user_id, user_email = isolated_app
        from app.extensions import db

        captured_calls = []
        original_get = db.session.get

        def _spy_get(model, ident, *args, **kwargs):
            captured_calls.append((model, ident))
            return original_get(model, ident, *args, **kwargs)

        with app.app_context():
            monkeypatch.setattr(db.session, "get", _spy_get)
            callback = app.login_manager._user_callback

            callback(str(user_id))
            callback(user_id)

            db.session.remove()

        assert len(captured_calls) == 2
        for model, ident in captured_calls:
            assert isinstance(ident, uuid.UUID), (
                f"esperava uuid.UUID entregue a db.session.get, recebi {type(ident).__name__}"
            )
