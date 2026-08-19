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

