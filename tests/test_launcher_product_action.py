"""Issue #59: o botão "Conhecer" de um produto sem acesso era um
`<a href="#">` puro - clicável, sem `onclick`, sem destino, indistinguível
de um link ativo para o usuário e para tecnologia assistiva. Decisão de
produto: as páginas comerciais de GEDO/Kalender/Hunt ainda não existem;
substitui-se o link por um controle "Em breve" realmente desabilitado
(`<button disabled>`), sem alterar `AccessService`, os estados de
assinatura ou as URLs configuradas dos produtos.

Estes testes cobrem apenas o HTML renderizado pelo launcher
(`dashboard.index` / `dashboard/launcher.html`) - não duplicam a cobertura
já existente de `AccessService.get_organization_products` (ver
`tests/test_access_service.py`)."""
import re
from datetime import datetime

from app.extensions import db
from app.models import Organization, OrganizationProduct, Product, User
from app.services.organization_service import OrganizationService

SYNTHETIC_PASSWORD = "senha-sintetica-issue-59-123"

# Casa exatamente um <button ...>Em breve</button>, com atributos em
# qualquer ordem (não depende de espaçamento/formatação incidental do
# template).
_DISABLED_BUTTON_RE = re.compile(r"<button([^>]*)>\s*Em breve\s*</button>")

# Casa exatamente um <a ...>Acessar Sistema</a>, capturando os atributos
# para inspeção isolada.
_ACCESS_LINK_RE = re.compile(r'<a([^>]*)>\s*Acessar Sistema\s*</a>')


def _create_user(email, *, name="Usuario Issue 59"):
    user = User(name=name, email=email, email_verified_at=datetime.utcnow())
    user.set_password(SYNTHETIC_PASSWORD)
    db.session.add(user)
    db.session.commit()
    return user


def _create_organization(legal_name="Organizacao Issue 59"):
    org = Organization(legal_name=legal_name)
    db.session.add(org)
    db.session.commit()
    return org


def _create_product(code, name, url="https://produto-issue-59.local"):
    product = Product(code=code, name=name, description=f"Descricao {name}", url=url)
    db.session.add(product)
    db.session.commit()
    return product


def _login(client, get_csrf_token, email):
    return client.post("/login", data={
        "email": email,
        "password": SYNTHETIC_PASSWORD,
        "csrf_token": get_csrf_token(client),
    })


class TestProductWithoutAccessShowsDisabledComingSoonControl:
    """Cenário 1: vínculo ativo, produto sem acesso (nenhuma linha de
    assinatura ainda)."""

    def test_shows_em_breve_as_real_disabled_control(self, client, app, get_csrf_token):
        with app.app_context():
            org = _create_organization("Organizacao Issue 59 Sem Acesso")
            user = _create_user("membro.semacesso.issue59@example.com")
            _create_product("produto-issue59-semacesso", "Produto Sem Acesso Issue 59")
            OrganizationService.add_member(org.id, user.id, "member")

        _login(client, get_csrf_token, "membro.semacesso.issue59@example.com")
        response = client.get("/")
        assert response.status_code == 200
        html = response.data.decode("utf-8")

        match = _DISABLED_BUTTON_RE.search(html)
        assert match is not None, "esperado <button ...>Em breve</button> no HTML"
        attrs = match.group(1)

        # Controle realmente desabilitado, não apenas com aparência: exige
        # o atributo booleano nativo `disabled` como token isolado - checar
        # apenas `"disabled" in attrs` seria satisfeito só por
        # `aria-disabled="true"` (substring), o que não comprova a
        # presença do atributo nativo. `attrs.split()` tokeniza por
        # espaço/quebra de linha, então não depende de ordem nem de
        # formatação incidental.
        assert "disabled" in attrs.split()
        assert 'aria-disabled="true"' in attrs

        # Nunca um link navegável: nem href, nem onclick, nem nova aba.
        assert "href" not in attrs
        assert "onclick" not in attrs
        assert "target" not in attrs

    def test_does_not_render_old_dead_link(self, client, app, get_csrf_token):
        with app.app_context():
            org = _create_organization("Organizacao Issue 59 Link Morto")
            user = _create_user("membro.linkmorto.issue59@example.com")
            _create_product("produto-issue59-linkmorto", "Produto Link Morto Issue 59")
            OrganizationService.add_member(org.id, user.id, "member")

        _login(client, get_csrf_token, "membro.linkmorto.issue59@example.com")
        response = client.get("/")
        html = response.data.decode("utf-8")

        # "Conhecer" só existia neste template (confirmado na investigação
        # da Issue #59) - a palavra não deve mais aparecer em lugar nenhum.
        assert "Conhecer" not in html
        assert 'href="#"' not in html


class TestProductWithActiveAccessKeepsAccessButton:
    """Cenário 2: vínculo ativo, produto concedido (status 'active')."""

    def test_keeps_acessar_sistema_with_configured_url_and_not_disabled(self, client, app, get_csrf_token):
        with app.app_context():
            org = _create_organization("Organizacao Issue 59 Com Acesso")
            user = _create_user("membro.comacesso.issue59@example.com")
            product = _create_product(
                "produto-issue59-comacesso", "Produto Com Acesso Issue 59",
                url="https://produto-issue59-comacesso.local",
            )
            OrganizationService.add_member(org.id, user.id, "member")
            org_product = OrganizationProduct(organization_id=org.id, product_id=product.id, status="active")
            db.session.add(org_product)
            db.session.commit()

        _login(client, get_csrf_token, "membro.comacesso.issue59@example.com")
        response = client.get("/")
        assert response.status_code == 200
        html = response.data.decode("utf-8")

        match = _ACCESS_LINK_RE.search(html)
        assert match is not None, "esperado <a ...>Acessar Sistema</a> no HTML"
        attrs = match.group(1)

        assert 'href="https://produto-issue59-comacesso.local"' in attrs
        assert 'target="_blank"' in attrs
        # Continua sendo um link ativo, nunca um controle desabilitado.
        assert "disabled" not in attrs

        assert "Em breve" not in html


class TestUnsubscribedAndRevokedProductsShareComingSoonPresentation:
    """Cenário 3: produto sem nenhuma linha de assinatura (unsubscribed) e
    produto com acesso revogado (status 'inactive') seguem a mesma regra
    de ausência de acesso já existente em `AccessService.has_access` -
    ambos devem apresentar "Em breve", sem distinção nova introduzida
    aqui."""

    def test_unsubscribed_and_revoked_both_show_em_breve(self, client, app, get_csrf_token):
        with app.app_context():
            org = _create_organization("Organizacao Issue 59 Unsub Revogado")
            user = _create_user("membro.unsubrevogado.issue59@example.com")
            unsubscribed_product = _create_product(
                "produto-issue59-semlinha", "Produto Sem Linha Issue 59",
            )
            revoked_product = _create_product(
                "produto-issue59-revogado", "Produto Revogado Issue 59",
            )
            OrganizationService.add_member(org.id, user.id, "member")
            # unsubscribed_product: nenhuma linha de OrganizationProduct é
            # criada de propósito (estado real de "nunca contratado").
            revoked_org_product = OrganizationProduct(
                organization_id=org.id, product_id=revoked_product.id, status="inactive",
            )
            db.session.add(revoked_org_product)
            db.session.commit()

        _login(client, get_csrf_token, "membro.unsubrevogado.issue59@example.com")
        response = client.get("/")
        assert response.status_code == 200
        html = response.data.decode("utf-8")

        assert "Produto Sem Linha Issue 59" in html
        assert "Produto Revogado Issue 59" in html

        # Duas ocorrências do controle desabilitado - uma por produto sem
        # acesso - e nenhum "Acessar Sistema" (nenhum dos dois tem acesso).
        assert len(_DISABLED_BUTTON_RE.findall(html)) == 2
        assert _ACCESS_LINK_RE.search(html) is None

        # Contagem exata, não só presença: comprova que AMBOS os cards
        # (produto sem linha e produto revogado) exibem o badge, não só um
        # deles - os dois caem no mesmo `{% else %}` do template
        # (apresentação preexistente que este ajuste não altera, apenas
        # comprova com mais rigor). Conta o atributo `class` completo do
        # badge, não a substring solta "status-unsubscribed" - esta também
        # aparece uma terceira vez como seletor no bloco <style> da
        # própria página, o que inflaria a contagem sem relação com os
        # cards renderizados.
        assert html.count('class="status-badge status-unsubscribed"') == 2


class TestUnlinkedUserStillSeesWaitingState:
    """Cenário 4: usuário sem organização vinculada continua vendo o
    estado de espera já existente - sem regressão introduzida por esta
    correção (que só toca a apresentação de produtos dentro do grid)."""

    def test_user_without_organization_sees_waiting_state_not_product_grid(self, client, app, get_csrf_token):
        with app.app_context():
            _create_user("membro.semorg.issue59@example.com")

        _login(client, get_csrf_token, "membro.semorg.issue59@example.com")
        response = client.get("/")
        assert response.status_code == 200
        html = response.data.decode("utf-8")

        assert "Aguardando vincula" in html
        assert "Em breve" not in html
        assert "Acessar Sistema" not in html
