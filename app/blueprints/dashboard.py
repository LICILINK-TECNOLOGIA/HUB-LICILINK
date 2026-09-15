from flask import Blueprint, flash, make_response, redirect, render_template, url_for
from flask_login import login_required, current_user
from ..services.organization_service import OrganizationService
from ..services.access_service import AccessService
from ..services.organization_product_installation_service import OrganizationProductInstallationService
from ..services.product_launch_code_service import ProductLaunchCodeError, ProductLaunchCodeService

dashboard_bp = Blueprint('dashboard', __name__)

# Issue #71: mesmo par de headers já usado por
# `app/blueprints/federation.py::_no_store_response` - reaproveitado
# aqui em vez de importado (a resposta desta rota é HTML renderizado,
# não JSON, e viveria num blueprint sem relação de domínio com
# federation.py; duplicar duas linhas de headers é mais simples e mais
# claro do que acoplar dashboard_bp a federation.py só por isso).
_NO_STORE_HEADERS = {
    'Cache-Control': 'no-store',
    'Pragma': 'no-cache',
    'Referrer-Policy': 'no-referrer',
}


@dashboard_bp.route('/')
@login_required
def index():
    # Issue #71: único ponto de resolução da organização corrente V1 -
    # mesmo helper usado por `launch` abaixo, nunca uma segunda
    # implementação de `orgs[0] if orgs else None` (ver
    # `OrganizationService.resolve_current_organization`).
    current_org = OrganizationService.resolve_current_organization(current_user.id)

    if current_org:
        launcher_items = AccessService.get_organization_products(current_user.id, current_org.id)
        # Issue #68/#71: consulta somente leitura, em lote (2 queries,
        # nunca uma por produto) - mesmo padrão já usado por
        # `app/blueprints/admin.py::org_details`. `AccessService`
        # permanece com sua responsabilidade única de status de
        # assinatura; o estado de instalação é combinado aqui, na rota.
        installations = OrganizationProductInstallationService.list_installations_by_organization(current_org.id)
    else:
        launcher_items = []
        installations = {}

    return render_template(
        'dashboard/launcher.html', org=current_org, items=launcher_items, installations=installations,
    )


@dashboard_bp.route('/launch/<string:product_code>', methods=['POST'])
@login_required
def launch(product_code):
    """Issue #71 (revisão técnica, achado A2): inicia o handoff federado -
    emite um código de lançamento de uso único e o transporta ao produto
    via um formulário HTML de auto-submit, nunca em URL/query string.

    `organization_id` nunca vem do cliente - resolvido exclusivamente
    por `OrganizationService.resolve_current_organization`, o mesmo
    helper usado por `index()` acima. `product_code` vem só do path e é
    encaminhado sem nenhuma resolução própria: `issue_launch_code`
    continua sendo a única fronteira autoritativa, revalidando
    integralmente usuário/vínculo/organização/assinatura/instalação a
    cada chamada.

    O `try` envolve exclusivamente a chamada a `issue_launch_code` -
    somente `ProductLaunchCodeError` (rejeição de domínio) é capturada,
    com a mensagem já curada pelo service e redirecionamento seguro.
    `ProductLaunchCodeOperationError` (falha operacional/de banco),
    qualquer falha na construção/renderização de `launch_handoff.html`
    (fora do `try`, já depois de uma emissão bem-sucedida - ver corpo da
    Issue #71, "Fronteira transacional: commit antes da renderização")
    e qualquer outra exceção não classificada NÃO são capturadas por
    esta rota - propagam para o tratamento padrão de exceção não
    tratada do Flask, nunca viram um `flash`/redirect de aparência
    saudável que mascararia uma indisponibilidade real como uma
    rejeição de domínio comum."""
    current_org = OrganizationService.resolve_current_organization(current_user.id)
    if current_org is None:
        flash('Não foi possível iniciar o acesso. Tente novamente.', 'error')
        return redirect(url_for('dashboard.index'))

    try:
        issuance = ProductLaunchCodeService.issue_launch_code(
            current_user.id, current_org.id, product_code,
        )
    except ProductLaunchCodeError as e:
        # Erro de domínio esperado e seguro - mensagem já curada pelo
        # service para exibição direta (nunca interpola valor bruto,
        # nunca revela organização/produto aos quais o usuário não tem
        # acesso).
        flash(str(e), 'error')
        return redirect(url_for('dashboard.index'))

    response = make_response(
        render_template(
            'dashboard/launch_handoff.html',
            code=issuance.code,
            destination_url=issuance.destination_url,
        )
    )
    response.headers.update(_NO_STORE_HEADERS)
    return response
