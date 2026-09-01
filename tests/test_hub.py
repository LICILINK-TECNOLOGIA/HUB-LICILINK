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
