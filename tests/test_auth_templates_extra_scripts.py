"""Issue #55: `base.html` não declarava `{% block extra_scripts %}`, então
os scripts definidos por `login.html`/`register.html`/`verify.html` eram
descartados silenciosamente pelo Jinja2 e nunca chegavam ao HTML entregue
ao navegador. Estes testes renderizam respostas Flask reais (via test
client) e inspecionam o HTML resultante - provam que os `<script>` estão
presentes, únicos, e que os elementos que eles referenciam existem no
mesmo HTML. Nenhum deles executa JavaScript de verdade (isso não é
alcançável a partir da suíte pytest existente, sem adicionar Selenium/
Playwright/Node/driver de navegador - fora do escopo desta correção). A
execução real do JavaScript no navegador será confirmada no reteste
manual, não por este arquivo.
"""
from flask import render_template_string

# Marcadores textuais únicos de cada script - usados para provar presença
# exata (uma vez) e ausência de vazamento entre páginas.
TOGGLE_SCRIPT_MARKER = "toggleButtons"
VERIFY_SCRIPT_MARKER = "updateHiddenInput"


# Presenca exata do script proprio de cada pagina -----------------------------

class TestScriptRenderedExactlyOnce:
    def test_login_contains_its_script_exactly_once(self, client):
        html = client.get("/login").data.decode("utf-8")
        # A tag <script> aparece exatamente uma vez na página inteira -
        # prova de que o bloco não é duplicado. `TOGGLE_SCRIPT_MARKER`
        # (nome da variável no script) naturalmente aparece mais de uma
        # vez *dentro* dessa única cópia (é declarada e depois usada) -
        # por isso a prova de unicidade é a contagem de tags, não do
        # marcador, que só precisa estar presente.
        assert html.count("<script>") == 1
        assert TOGGLE_SCRIPT_MARKER in html

    def test_register_contains_its_script_exactly_once(self, client):
        html = client.get("/register").data.decode("utf-8")
        assert html.count("<script>") == 1
        assert TOGGLE_SCRIPT_MARKER in html

    def test_verify_with_pending_session_contains_its_script_exactly_once(self, client):
        with client.session_transaction() as sess:
            sess["pending_registration_id"] = "id-sintetico-issue55"

        resp = client.get("/verify")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert html.count("<script>") == 1
        assert VERIFY_SCRIPT_MARKER in html


# Scripts nao vazam entre paginas ---------------------------------------------

class TestScriptsDoNotLeakBetweenPages:
    def test_login_does_not_contain_verify_script(self, client):
        html = client.get("/login").data.decode("utf-8")
        assert VERIFY_SCRIPT_MARKER not in html

    def test_register_does_not_contain_verify_script(self, client):
        html = client.get("/register").data.decode("utf-8")
        assert VERIFY_SCRIPT_MARKER not in html

    def test_verify_does_not_contain_toggle_password_script(self, client):
        with client.session_transaction() as sess:
            sess["pending_registration_id"] = "id-sintetico-issue55"

        html = client.get("/verify").data.decode("utf-8")
        assert "toggle-password" not in html
        assert TOGGLE_SCRIPT_MARKER not in html

    def test_page_without_extra_scripts_block_receives_no_auth_script(self, app):
        # Simula uma pagina que estende base.html sem definir
        # extra_scripts (como o restante do projeto: dashboard, admin
        # etc.) - o bloco vazio adicionado a base.html nao deve injetar
        # nada nela.
        with app.test_request_context():
            html = render_template_string(
                "{% extends 'base.html' %}{% block content %}Pagina sem scripts{% endblock %}"
            )
        assert "<script>" not in html
        assert TOGGLE_SCRIPT_MARKER not in html
        assert VERIFY_SCRIPT_MARKER not in html


# Elementos que os scripts referenciam continuam presentes -------------------

class TestReferencedElementsStillPresent:
    def test_login_toggle_buttons_have_type_button_and_aria_label(self, client):
        html = client.get("/login").data.decode("utf-8")
        assert 'class="toggle-password"' in html
        assert 'type="button" class="toggle-password"' in html
        assert 'aria-label="Mostrar senha"' in html

    def test_register_toggle_buttons_have_type_button_and_aria_label(self, client):
        html = client.get("/register").data.decode("utf-8")
        assert html.count('type="button" class="toggle-password"') == 2
        assert html.count('aria-label="Mostrar senha"') == 2

    def test_verify_has_six_code_inputs_and_hidden_code_field(self, client):
        with client.session_transaction() as sess:
            sess["pending_registration_id"] = "id-sintetico-issue55"

        html = client.get("/verify").data.decode("utf-8")
        assert html.count('class="code-input"') == 6
        assert '<input type="hidden" name="code" id="code-hidden">' in html

    def test_verify_submit_button_starts_disabled(self, client):
        with client.session_transaction() as sess:
            sess["pending_registration_id"] = "id-sintetico-issue55"

        html = client.get("/verify").data.decode("utf-8")
        assert 'id="submit-btn" disabled' in html

    def test_verify_script_contains_hidden_update_and_button_enable_logic(self, client):
        with client.session_transaction() as sess:
            sess["pending_registration_id"] = "id-sintetico-issue55"

        html = client.get("/verify").data.decode("utf-8")
        assert "hiddenInput.value = code" in html
        assert "submitBtn.disabled = code.length !== 6" in html

    def test_csrf_token_present_in_all_three_pages(self, client):
        with client.session_transaction() as sess:
            sess["pending_registration_id"] = "id-sintetico-issue55"

        for path in ("/login", "/register", "/verify"):
            html = client.get(path).data.decode("utf-8")
            assert 'name="csrf_token"' in html
