from app.routes import is_valid_url


def test_is_valid_url_in_production():
    # Em produção, HTTP deve ser rejeitado
    assert is_valid_url('https://sistema.exemplo.com', is_production=True) is True
    assert is_valid_url('https://sistema.exemplo.com/', is_production=True) is True

    assert is_valid_url('http://sistema.exemplo.com', is_production=True) is False
    assert is_valid_url('/sistema', is_production=True) is False
    assert is_valid_url('javascript:alert(1)', is_production=True) is False
    assert is_valid_url('data:text/html,', is_production=True) is False
    assert is_valid_url('', is_production=True) is False
    assert is_valid_url(None, is_production=True) is False
    assert is_valid_url('   ', is_production=True) is False

def test_is_valid_url_in_development():
    # Em desenvolvimento, HTTP pode ser aceito
    assert is_valid_url('https://sistema.exemplo.com', is_production=False) is True
    assert is_valid_url('http://sistema.exemplo.com', is_production=False) is True

    assert is_valid_url('/sistema', is_production=False) is False
    assert is_valid_url('javascript:alert(1)', is_production=False) is False

def test_index_route_requires_login(client):
    response = client.get('/')
    assert response.status_code == 302
    assert '/login' in response.location

def test_index_route_production_requires_login(prod_client):
    response = prod_client.get('/')
    assert response.status_code == 302
    assert '/login' in response.location

def test_no_open_redirect_via_query_string(client):
    # Acessando com url maliciosa na query string
    response = client.get('/login?url=https://malicioso.com&redirect=https://malicioso.com&next=https://malicioso.com')
    html = response.data.decode('utf-8')

    # A página não deve refletir ou usar essas urls em nenhum link de forma insegura
    assert 'href="https://malicioso.com"' not in html
