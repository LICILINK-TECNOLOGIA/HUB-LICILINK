from html.parser import HTMLParser

import pytest
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB

@compiles(JSONB, 'sqlite')
def compile_jsonb_sqlite(type_, compiler, **kw):
    return 'JSON'

from app import create_app


@pytest.fixture
def app():
    # Cria a app para testes (não produção, para aceitar HTTP)
    app = create_app('testing')
    app.config.update({
        'SECRET_KEY': 'test-key',
        'IS_PRODUCTION': False,
        'L_KALENDER_URL': 'http://kalender.local',
        'L_GEDO_URL': 'https://gedo.local/path'
    })
    
    # Contexto de aplicação para testes de banco
    from app.extensions import db
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()

@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture
def prod_app():
    # Cria a app simulando produção
    app = create_app('testing')
    app.config.update({
        'SECRET_KEY': 'prod-key',
        'IS_PRODUCTION': True,
        'L_KALENDER_URL': 'http://kalender.local',
        'L_GEDO_URL': 'https://gedo.local/path'
    })
    
    # Contexto de aplicação para testes de banco
    from app.extensions import db
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()

@pytest.fixture
def prod_client(prod_app):
    return prod_app.test_client()


class _CSRFTokenExtractor(HTMLParser):
    """Extrai o valor do primeiro <input name="csrf_token"> encontrado no
    HTML - parser simples da biblioteca padrão, não regex (frágil contra
    variação de atributos/quebras de linha) nem BeautiflSoup (dependência
    nova só para isto)."""

    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        if tag != "input" or self.token is not None:
            return
        attr_dict = dict(attrs)
        if attr_dict.get("name") == "csrf_token":
            self.token = attr_dict.get("value")


@pytest.fixture
def get_csrf_token():
    """Helper central (Issue #29): obtém um token CSRF legítimo fazendo um
    GET real de uma página com formulário renderizado, na MESMA instância
    de `client` recebida (mesmos cookies/sessão) - exatamente como um
    navegador real faria. Nunca lê ou gera o token por fora do fluxo HTTP
    normal (não acessa `session`/chave privada diretamente) e nunca
    desabilita CSRF. Um único token de sessão pode ser reaproveitado em
    qualquer POST protegido dentro da mesma sessão/cliente."""

    def _get(client, page_url="/login"):
        response = client.get(page_url)
        parser = _CSRFTokenExtractor()
        parser.feed(response.get_data(as_text=True))
        if not parser.token:
            raise AssertionError(
                f"Nenhum campo csrf_token encontrado na página {page_url!r} "
                f"(status HTTP {response.status_code}). Verifique se a rota "
                "renderiza um formulário protegido por CSRF."
            )
        return parser.token

    return _get

