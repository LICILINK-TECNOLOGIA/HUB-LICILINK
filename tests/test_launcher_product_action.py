"""Issue #59: o botão "Conhecer" de um produto sem acesso era um
`<a href="#">` puro - clicável, sem `onclick`, sem destino, indistinguível
de um link ativo para o usuário e para tecnologia assistiva. Decisão de
produto: as páginas comerciais de GEDO/Kalender/Hunt ainda não existem;
substitui-se o link por um controle "Em breve" realmente desabilitado
(`<button disabled>`), sem alterar `AccessService`, os estados de
assinatura ou as URLs configuradas dos produtos.

Issue #71: o antigo `<a href="{{ item.product.url }}" target="_blank">`
do estado com acesso é substituído por um `<form method="post">` local
que aciona `POST /launch/<product_code>` (handoff federado) - as
asserções que fixavam o link antigo são atualizadas deliberadamente
(não preservadas por acidente). Dois novos estados intermediários (sem
instalação / instalação inativa) também são cobertos aqui.

Estes testes cobrem apenas o HTML renderizado pelo launcher
(`dashboard.index` / `dashboard/launcher.html`) - não duplicam a cobertura
já existente de `AccessService.get_organization_products` (ver
`tests/test_access_service.py`) nem a suíte de
`ProductLaunchCodeService` (ver `tests/test_product_launch_code_service.py`)."""
import re
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import (
    Organization,
    OrganizationMember,
    OrganizationProduct,
    OrganizationProductInstallation,
    Product,
    User,
)
from app.services.organization_service import OrganizationService

SYNTHETIC_PASSWORD = "senha-sintetica-issue-59-123"

# Casa um <button ...>{texto}</button> disabled, com atributos em
# qualquer ordem (não depende de espaçamento/formatação incidental do
# template).
def _disabled_button_re(text):
    return re.compile(r"<button([^>]*)>\s*" + re.escape(text) + r"\s*</button>")


_EM_BREVE_RE = _disabled_button_re("Em breve")
_CONFIG_PENDENTE_RE = _disabled_button_re("Configuração pendente")
_AGUARDANDO_ATIVACAO_RE = _disabled_button_re("Aguardando ativação")

# Casa exatamente um <a ...>Acessar Sistema</a> - usado hoje só para
# comprovar AUSÊNCIA (Issue #71: o link direto foi substituído por um
# formulário local).
_ACCESS_LINK_RE = re.compile(r'<a([^>]*)>\s*Acessar Sistema\s*</a>')

# Localiza o bloco <form ...>...</form> cujo `action` aponta para a rota
# local de lançamento de um `product_code` específico - varre todos os
# formulários da página (inclusive o de logout da navbar) e devolve
# somente o que combina `action="/launch/<product_code>"` e
# `method="post"`, nunca dependendo de ser o único <form> presente.
def _find_launch_form(html, product_code):
    for attrs, inner in re.findall(r"<form([^>]*)>(.*?)</form>", html, re.DOTALL):
        if f'action="/launch/{product_code}"' in attrs and 'method="post"' in attrs:
            return attrs, inner
    return None


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


def _create_installation(org_product_id, url="https://instalacao-issue71.local", is_active=True):
    installation = OrganizationProductInstallation(
        organization_product_id=org_product_id, url=url, is_active=is_active,
    )
    db.session.add(installation)
    db.session.commit()
    return installation


def _set_membership_created_at(organization_id, user_id, created_at):
    """Fixa `created_at` do vínculo diretamente (Issue #71) - determinismo
    explícito para testes de ordenação, sem depender de `sleep` nem da
    resolução do relógio real entre duas chamadas de `add_member`."""
    membership = OrganizationMember.query.filter_by(
        organization_id=organization_id, user_id=user_id,
    ).one()
    membership.created_at = created_at
    db.session.commit()


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

        match = _EM_BREVE_RE.search(html)
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


class TestProductWithActiveAccessAndActiveInstallationShowsLaunchForm:
    """Cenário 2 (Issue #71): vínculo ativo, produto concedido (status
    'active') e instalação ativa - único estado efetivamente lançável.
    Substitui o antigo `<a href="{{ item.product.url }}" target="_blank">`
    por um `<form method="post">` para a rota local do HUB."""

    def test_renders_local_post_form_never_direct_link(self, client, app, get_csrf_token):
        with app.app_context():
            org = _create_organization("Organizacao Issue 71 Lancavel")
            user = _create_user("membro.lancavel.issue71@example.com")
            product = _create_product(
                "produto-issue71-lancavel", "Produto Lancavel Issue 71",
                url="https://url-legada-nunca-usada.local",
            )
            OrganizationService.add_member(org.id, user.id, "member")
            org_product = OrganizationProduct(organization_id=org.id, product_id=product.id, status="active")
            db.session.add(org_product)
            db.session.commit()
            _create_installation(org_product.id, url="https://instalacao-issue71-lancavel.local", is_active=True)
            product_code = product.code

        _login(client, get_csrf_token, "membro.lancavel.issue71@example.com")
        response = client.get("/")
        assert response.status_code == 200
        html = response.data.decode("utf-8")

        found = _find_launch_form(html, product_code)
        assert found is not None, "esperado <form method=\"post\" action=\"/launch/<product_code>\"> no HTML"
        attrs, inner = found

        # CSRF do HUB presente no formulário local.
        assert 'name="csrf_token"' in inner

        # Botão de submit real, nunca `type="button"` nem link.
        assert 'type="submit"' in inner
        assert "Acessar Sistema" in inner

        # Nunca a URL legada global do produto, nem em `action`, nem em
        # nenhum outro lugar do formulário/página.
        assert "url-legada-nunca-usada.local" not in html

        # Nunca a URL da instalação (destination_url) exposta no launcher
        # antes de qualquer emissão - só aparece depois, na página de
        # handoff, nunca aqui.
        assert "instalacao-issue71-lancavel.local" not in html

        # Nunca nova aba: o handoff deve permanecer na mesma navegação.
        assert "target" not in attrs

        # Nenhum link `<a>Acessar Sistema</a>` remanescente em lugar
        # nenhum da página (o antigo contrato foi substituído, não
        # duplicado).
        assert _ACCESS_LINK_RE.search(html) is None

        assert "Em breve" not in html


class TestProductWithActiveAccessButNoInstallationShowsPendingConfigState:
    """Cenário 2b (Issue #71): assinatura ativa, mas nenhuma
    `OrganizationProductInstallation` configurada ainda - estado
    "Configuração pendente", desabilitado, nunca o formulário de
    lançamento."""

    def test_shows_configuracao_pendente_disabled(self, client, app, get_csrf_token):
        with app.app_context():
            org = _create_organization("Organizacao Issue 71 Sem Instalacao")
            user = _create_user("membro.seminstalacao.issue71@example.com")
            product = _create_product(
                "produto-issue71-seminstalacao", "Produto Sem Instalacao Issue 71",
            )
            OrganizationService.add_member(org.id, user.id, "member")
            org_product = OrganizationProduct(organization_id=org.id, product_id=product.id, status="active")
            db.session.add(org_product)
            db.session.commit()
            product_code = product.code

        _login(client, get_csrf_token, "membro.seminstalacao.issue71@example.com")
        response = client.get("/")
        assert response.status_code == 200
        html = response.data.decode("utf-8")

        match = _CONFIG_PENDENTE_RE.search(html)
        assert match is not None, "esperado <button ...>Configuração pendente</button> no HTML"
        attrs = match.group(1)
        assert "disabled" in attrs.split()
        assert 'aria-disabled="true"' in attrs

        assert _find_launch_form(html, product_code) is None
        assert _ACCESS_LINK_RE.search(html) is None
        assert "Em breve" not in html


class TestProductWithActiveAccessAndInactiveInstallationShowsWaitingActivationState:
    """Cenário 2c (Issue #71): assinatura ativa, instalação já
    configurada mas `is_active=False` - estado "Aguardando ativação",
    desabilitado, nunca o formulário de lançamento."""

    def test_shows_aguardando_ativacao_disabled(self, client, app, get_csrf_token):
        with app.app_context():
            org = _create_organization("Organizacao Issue 71 Instalacao Inativa")
            user = _create_user("membro.instalacaoinativa.issue71@example.com")
            product = _create_product(
                "produto-issue71-instalacaoinativa", "Produto Instalacao Inativa Issue 71",
            )
            OrganizationService.add_member(org.id, user.id, "member")
            org_product = OrganizationProduct(organization_id=org.id, product_id=product.id, status="active")
            db.session.add(org_product)
            db.session.commit()
            _create_installation(org_product.id, is_active=False)
            product_code = product.code

        _login(client, get_csrf_token, "membro.instalacaoinativa.issue71@example.com")
        response = client.get("/")
        assert response.status_code == 200
        html = response.data.decode("utf-8")

        match = _AGUARDANDO_ATIVACAO_RE.search(html)
        assert match is not None, "esperado <button ...>Aguardando ativação</button> no HTML"
        attrs = match.group(1)
        assert "disabled" in attrs.split()
        assert 'aria-disabled="true"' in attrs

        assert _find_launch_form(html, product_code) is None
        assert _ACCESS_LINK_RE.search(html) is None


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
        assert len(_EM_BREVE_RE.findall(html)) == 2
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


class TestMultipleOrganizationsFollowDeterministicV1Rule:
    """Issue #71 (achado A2): usuário com vínculo ativo em DUAS
    organizações - o launcher deve sempre listar os produtos da
    organização cujo vínculo tem o `created_at` mais antigo (regra V1
    determinística de `OrganizationService.resolve_current_organization`),
    nunca uma organização escolhida por ordem incidental do banco.
    `created_at` é fixado explicitamente (sem `sleep`, sem depender do
    relógio real) para que o teste seja determinístico mesmo que as duas
    linhas sejam inseridas na mesma transação/millisegundo."""

    def test_launcher_lists_products_of_the_earliest_joined_organization(self, client, app, get_csrf_token):
        with app.app_context():
            org_recent = _create_organization("Organizacao Issue 71 Multi Recente")
            org_earliest = _create_organization("Organizacao Issue 71 Multi Antiga")
            user = _create_user("membro.multiorg.issue71@example.com")

            product_recent = _create_product(
                "produto-issue71-multi-recente", "Produto Multi Org Recente Issue 71",
            )
            product_earliest = _create_product(
                "produto-issue71-multi-antiga", "Produto Multi Org Antiga Issue 71",
            )

            # Ordem de inserção deliberadamente DIVERGENTE da ordem lógica
            # esperada: `org_recent`/seu vínculo é criado PRIMEIRO no
            # banco, mas recebe o `created_at` mais NOVO - comprova que a
            # resolução segue `created_at`, nunca a ordem de inserção
            # física/PK.
            OrganizationService.add_member(org_recent.id, user.id, "member")
            OrganizationService.add_member(org_earliest.id, user.id, "member")

            base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
            _set_membership_created_at(org_recent.id, user.id, base_time + timedelta(days=1))
            _set_membership_created_at(org_earliest.id, user.id, base_time)

            org_product_recent = OrganizationProduct(
                organization_id=org_recent.id, product_id=product_recent.id, status="active",
            )
            org_product_earliest = OrganizationProduct(
                organization_id=org_earliest.id, product_id=product_earliest.id, status="active",
            )
            db.session.add_all([org_product_recent, org_product_earliest])
            db.session.commit()
            _create_installation(org_product_earliest.id, is_active=True)
            _create_installation(org_product_recent.id, is_active=True)

        _login(client, get_csrf_token, "membro.multiorg.issue71@example.com")
        response = client.get("/")
        assert response.status_code == 200
        html = response.data.decode("utf-8")

        # O cabeçalho do launcher identifica a organização corrente
        # resolvida - deve ser sempre a mais antiga por vínculo
        # (`org_earliest`), nunca `org_recent`.
        assert "Organizacao Issue 71 Multi Antiga" in html

        # `AccessService.get_organization_products` lista o catálogo
        # GLOBAL de produtos para a organização corrente (comportamento
        # preexistente, não alterado por esta Issue) - por isso os DOIS
        # nomes de produto aparecem no HTML independentemente da
        # organização resolvida. O sinal real de qual organização foi
        # resolvida é qual dos dois é efetivamente LANÇÁVEL: só o produto
        # vinculado a `org_earliest` deve ter o formulário POST local;
        # o de `org_recent` deve cair no estado "Em breve" (a
        # organização corrente não tem assinatura para ele).
        assert _find_launch_form(html, "produto-issue71-multi-antiga") is not None
        assert _find_launch_form(html, "produto-issue71-multi-recente") is None
