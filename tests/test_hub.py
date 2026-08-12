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

def test_index_route(client):
    response = client.get('/')
    assert response.status_code == 200
    html = response.data.decode('utf-8')

    # Verifica se os elementos principais da página existem
    assert 'HUB de Sistemas LiciLink' in html
    assert 'L-Kalender' in html
    assert 'L-GeDo' in html

    # Verifica se as urls mockadas no conftest aparecem para a app não-prod
    assert 'http://kalender.local' in html
    assert 'https://gedo.local/path' in html

    # Verifica a proteção de open links
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html

def test_index_route_production(prod_client):
    response = prod_client.get('/')
    assert response.status_code == 200
    html = response.data.decode('utf-8')

    # Em produção, kalender.local com http deve ser marcado como indisponível
    # Não deve ter o link http://kalender.local no href
    assert 'href="http://kalender.local"' not in html
    assert 'Acesso indisponível' in html

    # L-GeDo deve estar disponível pois usa https
    assert 'href="https://gedo.local/path"' in html

def test_no_open_redirect_via_query_string(client):
    # Acessando com url maliciosa na query string
    response = client.get('/?url=https://malicioso.com&redirect=https://malicioso.com&next=https://malicioso.com')
    html = response.data.decode('utf-8')

    # A página não deve refletir ou usar essas urls em nenhum link
    assert 'malicioso.com' not in html
    # Os links originais das variáveis de ambiente devem permanecer inalterados
    assert 'http://kalender.local' in html
