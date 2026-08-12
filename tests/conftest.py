import pytest

from app import create_app


@pytest.fixture
def app():
    # Cria a app para testes (não produção, para aceitar HTTP)
    class TestConfig:
        TESTING = True
        SECRET_KEY = 'test-key'
        IS_PRODUCTION = False
        L_KALENDER_URL = 'http://kalender.local'
        L_GEDO_URL = 'https://gedo.local/path'

    app = create_app(TestConfig)
    yield app

@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture
def prod_app():
    # Cria a app simulando produção
    class ProdConfig:
        TESTING = True
        SECRET_KEY = 'prod-key'
        IS_PRODUCTION = True
        L_KALENDER_URL = 'http://kalender.local' # Inválido em prod
        L_GEDO_URL = 'https://gedo.local/path'   # Válido em prod

    app = create_app(ProdConfig)
    yield app

@pytest.fixture
def prod_client(prod_app):
    return prod_app.test_client()
