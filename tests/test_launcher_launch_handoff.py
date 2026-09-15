"""Issue #71: `app/templates/dashboard/launch_handoff.html` - a página
intermediária de auto-submit que transporta o código de lançamento
recém-emitido ao produto federado via `POST` cross-origin.

Analisa o HTML real da resposta com um parser estrutural
(`html.parser.HTMLParser`, biblioteca padrão - nenhuma dependência nova),
nunca por substring solta, para provar exatamente o que está e o que não
está presente: um único formulário externo, um único campo federado
`code`, nenhum CSRF do HUB, nenhum dado de organização/produto/
credencial, nenhuma cópia adicional do código, script estático (nunca
inline) sem interpolação."""
import re
import uuid
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from app.extensions import db
from app.models import (
    Organization,
    OrganizationProduct,
    OrganizationProductInstallation,
    Product,
    User,
)
from app.services.organization_service import OrganizationService

SYNTHETIC_PASSWORD = "senha-sintetica-issue-71-handoff"


class _HandoffPageParser(HTMLParser):
    """Extrai formulários (com seus `<input>`/`<button>` filhos),
    `<script>` (atributos + texto inline) e todo o texto visível da
    página - o suficiente para as asserções estruturais deste arquivo,
    sem precisar de nenhuma biblioteca externa de parsing/DOM."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.scripts = []
        self.text_chunks = []
        self._current_form = None
        self._current_script = None
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == 'form':
            self._current_form = {'attrs': attrs_dict, 'inputs': [], 'buttons': []}
            self.forms.append(self._current_form)
        elif tag == 'input' and self._current_form is not None:
            self._current_form['inputs'].append(attrs_dict)
        elif tag == 'button' and self._current_form is not None:
            self._current_form['buttons'].append({'attrs': attrs_dict, 'text': ''})
        elif tag == 'script':
            self._current_script = {'attrs': attrs_dict, 'text': ''}
            self.scripts.append(self._current_script)
            self._in_script = True

    def handle_endtag(self, tag):
        if tag == 'form':
            self._current_form = None
        elif tag == 'script':
            self._in_script = False
            self._current_script = None

    def handle_data(self, data):
        if self._in_script and self._current_script is not None:
            self._current_script['text'] += data
            return
        self.text_chunks.append(data)
        if self._current_form is not None and self._current_form['buttons']:
            self._current_form['buttons'][-1]['text'] += data


def _parse(html_text):
    parser = _HandoffPageParser()
    parser.feed(html_text)
    return parser


def _create_user(email=None):
    user = User(
        name="Usuario Issue 71 Handoff",
        email=email or f"handoff.issue71.{uuid.uuid4().hex[:8]}@example.test",
        email_verified_at=datetime.utcnow(),
    )
    user.set_password(SYNTHETIC_PASSWORD)
    db.session.add(user)
    db.session.commit()
    return user


def _create_organization():
    org = Organization(legal_name=f"Organizacao Issue 71 Handoff {uuid.uuid4().hex[:8]}", is_active=True)
    db.session.add(org)
    db.session.commit()
    return org


def _create_product(code="gedo", url="https://produto-legado-issue-71-handoff.local"):
    product = Product(code=code, name="Produto Issue 71 Handoff", description="Descricao", url=url)
    db.session.add(product)
    db.session.commit()
    return product


def _build_launchable_chain(installation_url="https://instalacao-issue71-handoff.local"):
    user = _create_user()
    organization = _create_organization()
    OrganizationService.add_member(organization.id, user.id, "member")
    product = _create_product()
    org_product = OrganizationProduct(organization_id=organization.id, product_id=product.id, status="active")
    db.session.add(org_product)
    db.session.commit()
    installation = OrganizationProductInstallation(
        organization_product_id=org_product.id, url=installation_url, is_active=True,
    )
    db.session.add(installation)
    db.session.commit()
    return {"user": user, "organization": organization, "product": product, "installation": installation}


def _login(client, get_csrf_token, email):
    return client.post("/login", data={
        "email": email,
        "password": SYNTHETIC_PASSWORD,
        "csrf_token": get_csrf_token(client),
    })


def _launch(client, get_csrf_token, product_code):
    token = get_csrf_token(client, "/")
    return client.post(f"/launch/{product_code}", data={"csrf_token": token})


class TestExactlyOneExternalForm:
    def test_exactly_one_form_targets_the_destination_url_with_post(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_launchable_chain()
            product_code = chain["product"].code
            email = chain["user"].email
            expected_destination = chain["installation"].url

        _login(client, get_csrf_token, email)
        response = _launch(client, get_csrf_token, product_code)
        assert response.status_code == 200
        page = _parse(response.data.decode("utf-8"))

        assert len(page.forms) == 1
        form = page.forms[0]
        assert form['attrs'].get('method', '').lower() == 'post'
        assert form['attrs'].get('action') == expected_destination

    def test_form_uses_default_url_encoded_body_not_multipart(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_launchable_chain()
            product_code = chain["product"].code
            email = chain["user"].email

        _login(client, get_csrf_token, email)
        response = _launch(client, get_csrf_token, product_code)
        page = _parse(response.data.decode("utf-8"))

        form = page.forms[0]
        enctype = form['attrs'].get('enctype')
        assert enctype in (None, 'application/x-www-form-urlencoded')


class TestExactlyOneFederatedCodeField:
    def test_exactly_one_hidden_input_named_code_with_the_emitted_value(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_launchable_chain()
            product_code = chain["product"].code
            email = chain["user"].email

        _login(client, get_csrf_token, email)
        response = _launch(client, get_csrf_token, product_code)
        raw_html = response.data.decode("utf-8")
        page = _parse(raw_html)

        form = page.forms[0]
        code_inputs = [i for i in form['inputs'] if i.get('name') == 'code']
        assert len(code_inputs) == 1
        assert code_inputs[0].get('type') == 'hidden'
        code_value = code_inputs[0].get('value')
        assert code_value is not None and len(code_value) > 0

        # O valor aparece exatamente uma vez no documento inteiro (não
        # duplicado em outro lugar, ex.: comentário, outro input, texto).
        assert raw_html.count(code_value) == 1

    def test_code_is_never_visible_text(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_launchable_chain()
            product_code = chain["product"].code
            email = chain["user"].email

        _login(client, get_csrf_token, email)
        response = _launch(client, get_csrf_token, product_code)
        raw_html = response.data.decode("utf-8")
        page = _parse(raw_html)

        code_value = page.forms[0]['inputs'][
            [i.get('name') for i in page.forms[0]['inputs']].index('code')
        ]['value']

        visible_text = "".join(page.text_chunks)
        assert code_value not in visible_text

    def test_code_not_in_comment_data_attribute_title_or_script(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_launchable_chain()
            product_code = chain["product"].code
            email = chain["user"].email

        _login(client, get_csrf_token, email)
        response = _launch(client, get_csrf_token, product_code)
        raw_html = response.data.decode("utf-8")
        page = _parse(raw_html)

        code_value = page.forms[0]['inputs'][
            [i.get('name') for i in page.forms[0]['inputs']].index('code')
        ]['value']

        assert "<!--" not in raw_html or code_value not in raw_html.split("<!--", 1)[1].split("-->", 1)[0]
        for script in page.scripts:
            assert code_value not in script['text']
        title_match = re.search(r"<title>(.*?)</title>", raw_html, re.DOTALL)
        assert title_match is not None
        assert code_value not in title_match.group(1)
        for form in page.forms:
            for key, value in form['attrs'].items():
                if key.startswith('data-'):
                    assert code_value not in (value or '')


class TestNoCsrfOrHubSecretsInExternalForm:
    def test_no_csrf_token_field_in_the_external_form(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_launchable_chain()
            product_code = chain["product"].code
            email = chain["user"].email

        _login(client, get_csrf_token, email)
        response = _launch(client, get_csrf_token, product_code)
        page = _parse(response.data.decode("utf-8"))

        form = page.forms[0]
        field_names = {i.get('name') for i in form['inputs']}
        assert 'csrf_token' not in field_names

    def test_launcher_csrf_token_value_never_appears_in_the_handoff_response(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_launchable_chain()
            product_code = chain["product"].code
            email = chain["user"].email

        _login(client, get_csrf_token, email)
        launcher_token = get_csrf_token(client, "/")
        response = client.post(f"/launch/{product_code}", data={"csrf_token": launcher_token})
        raw_html = response.data.decode("utf-8")

        assert launcher_token not in raw_html

    def test_no_organization_product_code_or_credential_fields(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_launchable_chain()
            product_code = chain["product"].code
            email = chain["user"].email

        _login(client, get_csrf_token, email)
        response = _launch(client, get_csrf_token, product_code)
        page = _parse(response.data.decode("utf-8"))

        form = page.forms[0]
        field_names = {i.get('name') for i in form['inputs']}
        assert field_names == {'code'}

    def test_legacy_product_url_never_appears_in_the_response(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_launchable_chain()
            product_code = chain["product"].code
            email = chain["user"].email
            legacy_url = chain["product"].url

        _login(client, get_csrf_token, email)
        response = _launch(client, get_csrf_token, product_code)
        raw_html = response.data.decode("utf-8")

        assert legacy_url not in raw_html


class TestAutoSubmitScriptIsStaticAndDedicated:
    def test_script_is_external_with_defer_never_inline(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_launchable_chain()
            product_code = chain["product"].code
            email = chain["user"].email

        _login(client, get_csrf_token, email)
        response = _launch(client, get_csrf_token, product_code)
        page = _parse(response.data.decode("utf-8"))

        assert len(page.scripts) == 1
        script = page.scripts[0]
        assert script['attrs'].get('src')
        assert 'defer' in script['attrs']
        # Nenhum JavaScript inline - o `<script src=...>` não carrega
        # nenhum texto entre as tags.
        assert script['text'].strip() == ''

    def test_static_script_file_never_interpolates_code_or_destination(self):
        script_path = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "launch_handoff.js"
        source = script_path.read_text(encoding="utf-8")

        # O arquivo estático é o mesmo para todo mundo (nunca renderizado
        # pelo Jinja) - nenhuma referência a `code`/`destination_url`
        # nem concatenação de URL, nenhum `fetch`, nenhuma query string.
        assert "{{" not in source
        assert "fetch(" not in source
        assert "destination_url" not in source
        assert "?" not in source

    def test_fallback_button_submits_the_same_already_rendered_form(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_launchable_chain()
            product_code = chain["product"].code
            email = chain["user"].email

        _login(client, get_csrf_token, email)
        response = _launch(client, get_csrf_token, product_code)
        page = _parse(response.data.decode("utf-8"))

        form = page.forms[0]
        assert len(form['buttons']) == 1
        button = form['buttons'][0]
        assert button['attrs'].get('type') == 'submit'
        assert button['text'].strip() != ''
        # O botão não tem `onclick`/`formaction` próprios - reenvia o
        # MESMO formulário, nunca dispara uma requisição separada.
        assert 'onclick' not in button['attrs']
        assert 'formaction' not in button['attrs']


class TestResponseHeaders:
    def test_headers_are_present_on_the_handoff_response(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_launchable_chain()
            product_code = chain["product"].code
            email = chain["user"].email

        _login(client, get_csrf_token, email)
        response = _launch(client, get_csrf_token, product_code)

        assert response.headers.get("Cache-Control") == "no-store"
        assert response.headers.get("Pragma") == "no-cache"
        assert response.headers.get("Referrer-Policy") == "no-referrer"


class TestNoCodeOutsideTheHandoffField:
    def test_no_code_in_response_location_cookies_or_set_cookie_headers(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_launchable_chain()
            product_code = chain["product"].code
            email = chain["user"].email

        _login(client, get_csrf_token, email)
        response = _launch(client, get_csrf_token, product_code)
        page = _parse(response.data.decode("utf-8"))

        code_value = page.forms[0]['inputs'][
            [i.get('name') for i in page.forms[0]['inputs']].index('code')
        ]['value']

        assert "Location" not in response.headers
        for set_cookie in response.headers.get_all("Set-Cookie") if "Set-Cookie" in response.headers else []:
            assert code_value not in set_cookie


class TestDestinationUrlEscapingAndShape:
    def test_ampersand_in_path_is_html_escaped_but_semantically_preserved(self, client, app, get_csrf_token):
        installation_url = "https://instalacao-issue71-escaping.local/caminho&extra"
        with app.app_context():
            chain = _build_launchable_chain(installation_url=installation_url)
            product_code = chain["product"].code
            email = chain["user"].email

        _login(client, get_csrf_token, email)
        response = _launch(client, get_csrf_token, product_code)
        raw_html = response.data.decode("utf-8")

        # Comprova que o escaping automático do Jinja realmente ocorreu
        # (nunca um `&` cru dentro do atributo `action`).
        assert "caminho&amp;extra" in raw_html
        assert "caminho&extra\"" not in raw_html

        page = _parse(raw_html)
        # `HTMLParser` decodifica entidades HTML nos valores de atributo
        # automaticamente - o valor semântico do destino continua
        # exatamente igual ao `destination_url` original.
        assert page.forms[0]['attrs'].get('action') == installation_url

    def test_destination_url_with_path_is_used_verbatim(self, client, app, get_csrf_token):
        installation_url = "https://instalacao-issue71-comcaminho.local/app/entrada"
        with app.app_context():
            chain = _build_launchable_chain(installation_url=installation_url)
            product_code = chain["product"].code
            email = chain["user"].email

        _login(client, get_csrf_token, email)
        response = _launch(client, get_csrf_token, product_code)
        page = _parse(response.data.decode("utf-8"))

        assert page.forms[0]['attrs'].get('action') == installation_url

    def test_form_never_opens_a_new_tab(self, client, app, get_csrf_token):
        with app.app_context():
            chain = _build_launchable_chain()
            product_code = chain["product"].code
            email = chain["user"].email

        _login(client, get_csrf_token, email)
        response = _launch(client, get_csrf_token, product_code)
        page = _parse(response.data.decode("utf-8"))

        assert 'target' not in page.forms[0]['attrs']
