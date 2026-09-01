import uuid

from flask import Blueprint, current_app, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user
from ..decorators import internal_admin_required
from ..models.identity import User, Organization
from ..models.crm import Lead
from ..extensions import db
from ..services.access_service import AccessService, ProductAccessError, ProductAccessOperationError

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def _parse_form_uuid(field_name):
    """Converte um campo de formulário (sempre string) para `uuid.UUID`
    estrito - levanta `TypeError`/`ValueError` se ausente ou malformado,
    sem normalizar, sem aceitar valor parcial/prefixo e sem nenhum
    fallback. Nunca loga nem inclui o valor recebido em nenhuma mensagem
    (quem chama decide a mensagem segura e o redirect); usado pelas rotas
    cujo campo de UUID vem de formulário, nunca do conversor `<uuid:...>`
    da própria URL (esse já é convertido automaticamente pelo Flask antes
    da view rodar)."""
    return uuid.UUID(request.form.get(field_name))

@admin_bp.before_request
@login_required
@internal_admin_required
def require_internal_admin():
    pass

@admin_bp.route('/')
def dashboard():
    users_count = User.query.count()
    orgs_count = Organization.query.count()
    leads_count = Lead.query.count()
    return render_template('admin/dashboard.html', 
                           users_count=users_count, 
                           orgs_count=orgs_count, 
                           leads_count=leads_count)

@admin_bp.route('/users-orgs')
def users_orgs():
    users = User.query.all()
    orgs = Organization.query.all()
    roles = Role.query.all()
    return render_template('admin/users_orgs.html', users=users, orgs=orgs, roles=roles)

@admin_bp.route('/crm')
def crm_leads():
    leads = Lead.query.order_by(Lead.created_at.desc()).all()
    return render_template('admin/crm_leads.html', leads=leads)

from ..services.organization_service import OrganizationService, OrganizationError, OrganizationOperationError
from ..models.authorization import Role

@admin_bp.route('/organizations/new', methods=['GET'])
def new_organization():
    return render_template('admin/org_form.html')

@admin_bp.route('/organizations', methods=['POST'])
def create_organization():
    legal_name = request.form.get('legal_name')
    trade_name = request.form.get('trade_name')
    cnpj = request.form.get('cnpj')
    email = request.form.get('email')
    phone = request.form.get('phone')
    
    if not legal_name:
        flash('Razão Social é obrigatória.', 'error')
        return redirect(url_for('admin.new_organization'))
        
    try:
        org = OrganizationService.create_organization(legal_name, trade_name, cnpj, email, phone)
        flash('Organização criada com sucesso.', 'success')
        return redirect(url_for('admin.org_details', org_id=org.id))
    except OrganizationOperationError as e:
        current_app.logger.exception('Falha inesperada ao criar organização')
        flash(str(e), 'error')
    except OrganizationError as e:
        flash(str(e), 'error')
    except Exception:
        current_app.logger.exception('Falha inesperada e não classificada ao criar organização')
        flash('Não foi possível criar a organização. Tente novamente.', 'error')
    return redirect(url_for('admin.new_organization'))

@admin_bp.route('/organizations/<uuid:org_id>', methods=['GET'])
def org_details(org_id):
    org = Organization.query.get_or_404(org_id)
    roles = Role.query.all()
    users = User.query.all()
    products = AccessService.list_organization_products_for_admin(org.id)
    return render_template('admin/org_details.html', org=org, roles=roles, users=users, products=products)

@admin_bp.route('/organizations/<uuid:org_id>/members', methods=['POST'])
def add_member(org_id):
    role_name = request.form.get('role')
    try:
        # user_id vem de campo de formulário (sempre string) - diferente de
        # org_id, já convertido para uuid.UUID pelo conversor <uuid:...> da
        # própria URL. Sem esta conversão explícita, uma string (mesmo que
        # um UUID válido) quebra o bind de parâmetro de qualquer query
        # filtrada por esta coluna no SQLite (o bind processor do
        # SQLAlchemy para UUID(as_uuid=True) sempre espera um objeto
        # `uuid.UUID` já pronto) - nunca afeta o PostgreSQL real, cujo
        # driver aceita a string diretamente, mas precisa ser tratado aqui
        # para a rota nunca depender desse comportamento divergente entre
        # ambientes.
        user_id = _parse_form_uuid('user_id')
    except (TypeError, ValueError):
        flash('Usuário inválido.', 'error')
        return redirect(url_for('admin.org_details', org_id=org_id))
    try:
        OrganizationService.add_member(org_id, user_id, role_name)
        flash('Membro adicionado com sucesso.', 'success')
    except OrganizationOperationError as e:
        current_app.logger.exception(
            'Falha inesperada ao adicionar membro (org_id=%s, user_id=%s)',
            org_id, user_id,
        )
        flash(str(e), 'error')
    except OrganizationError as e:
        flash(str(e), 'error')
    except Exception:
        current_app.logger.exception(
            'Falha inesperada e não classificada ao adicionar membro (org_id=%s, user_id=%s)',
            org_id, user_id,
        )
        flash('Não foi possível adicionar o membro. Tente novamente.', 'error')
    return redirect(url_for('admin.org_details', org_id=org_id))

@admin_bp.route('/organizations/<uuid:org_id>/members/<uuid:user_id>/role', methods=['POST'])
def change_member_role(org_id, user_id):
    role_name = request.form.get('role')
    try:
        OrganizationService.change_member_role(org_id, user_id, role_name)
        flash('Papel alterado com sucesso.', 'success')
    except OrganizationOperationError as e:
        current_app.logger.exception(
            'Falha inesperada ao alterar papel do membro (org_id=%s, user_id=%s)',
            org_id, user_id,
        )
        flash(str(e), 'error')
    except OrganizationError as e:
        flash(str(e), 'error')
    except Exception:
        current_app.logger.exception(
            'Falha inesperada e não classificada ao alterar papel do membro (org_id=%s, user_id=%s)',
            org_id, user_id,
        )
        flash('Não foi possível alterar o papel do membro. Tente novamente.', 'error')
    return redirect(url_for('admin.org_details', org_id=org_id))

@admin_bp.route('/organizations/<uuid:org_id>/members/<uuid:user_id>/remove', methods=['POST'])
def remove_member(org_id, user_id):
    try:
        OrganizationService.remove_member(org_id, user_id)
        flash('Membro removido com sucesso.', 'success')
    except OrganizationOperationError as e:
        current_app.logger.exception(
            'Falha inesperada ao remover membro (org_id=%s, user_id=%s)',
            org_id, user_id,
        )
        flash(str(e), 'error')
    except OrganizationError as e:
        flash(str(e), 'error')
    except Exception:
        current_app.logger.exception(
            'Falha inesperada e não classificada ao remover membro (org_id=%s, user_id=%s)',
            org_id, user_id,
        )
        flash('Não foi possível remover o membro. Tente novamente.', 'error')
    return redirect(url_for('admin.org_details', org_id=org_id))

@admin_bp.route('/users/<uuid:user_id>/organization', methods=['POST'])
def link_user_to_org(user_id):
    role_name = request.form.get('role', 'owner')
    try:
        # Mesmo motivo do parse em add_member: organization_id vem de campo
        # de formulário (string) - diferente de user_id nesta rota
        # (parâmetro da própria URL, <uuid:user_id>, já convertido pelo
        # Flask antes desta view rodar).
        org_id = _parse_form_uuid('organization_id')
    except (TypeError, ValueError):
        flash('Organização inválida.', 'error')
        return redirect(url_for('admin.users_orgs'))
    try:
        OrganizationService.add_member(org_id, user_id, role_name)
        flash('Usuário vinculado com sucesso.', 'success')
    except OrganizationOperationError as e:
        current_app.logger.exception(
            'Falha inesperada ao vincular usuário a organização (org_id=%s, user_id=%s)',
            org_id, user_id,
        )
        flash(str(e), 'error')
    except OrganizationError as e:
        flash(str(e), 'error')
    except Exception:
        current_app.logger.exception(
            'Falha inesperada e não classificada ao vincular usuário a organização (org_id=%s, user_id=%s)',
            org_id, user_id,
        )
        flash('Não foi possível vincular o usuário à organização. Tente novamente.', 'error')
    return redirect(url_for('admin.users_orgs'))


@admin_bp.route('/organizations/<uuid:org_id>/products/<string:product_code>/grant', methods=['POST'])
def grant_product(org_id, product_code):
    # Confirma a organização antes de qualquer mutação - 404 explícito para
    # UUID válido de organização inexistente (UUID sintaticamente inválido
    # já vira 404 pelo conversor <uuid:...> da rota, antes deste código
    # rodar). O formulário nunca envia status nem product_id - apenas o
    # product_code, que o service revalida contra o catálogo canônico.
    Organization.query.get_or_404(org_id)
    try:
        result = AccessService.grant_product_access(org_id, product_code, actor_user_id=current_user.id)
        if result.changed:
            flash('Acesso ao produto concedido com sucesso.', 'success')
        else:
            flash('A organização já possuía acesso ativo a este produto.', 'success')
    except ProductAccessOperationError as e:
        # Falha inesperada (banco/driver/AuditLog): a causa técnica real
        # (`e.__cause__`) vai só para o log do servidor, nunca para o
        # usuário - `str(e)` aqui já é a mensagem genérica pré-definida
        # pelo AccessService, nunca o texto da exceção original.
        current_app.logger.exception(
            'Falha inesperada ao conceder acesso a produto (org_id=%s, product_code=%s)',
            org_id, product_code,
        )
        flash(str(e), 'error')
    except ProductAccessError as e:
        # Erro de domínio esperado e seguro - mensagem já curada para
        # exibição direta, sem stack trace.
        flash(str(e), 'error')
    except Exception:
        current_app.logger.exception(
            'Falha inesperada e não classificada ao conceder acesso a produto (org_id=%s, product_code=%s)',
            org_id, product_code,
        )
        flash('Não foi possível concluir a operação. Tente novamente.', 'error')
    return redirect(url_for('admin.org_details', org_id=org_id))


@admin_bp.route('/organizations/<uuid:org_id>/products/<string:product_code>/revoke', methods=['POST'])
def revoke_product(org_id, product_code):
    Organization.query.get_or_404(org_id)
    try:
        result = AccessService.revoke_product_access(org_id, product_code, actor_user_id=current_user.id)
        if result.changed:
            flash('Acesso ao produto revogado com sucesso.', 'success')
        else:
            flash('A organização já não possuía acesso a este produto.', 'success')
    except ProductAccessOperationError as e:
        current_app.logger.exception(
            'Falha inesperada ao revogar acesso a produto (org_id=%s, product_code=%s)',
            org_id, product_code,
        )
        flash(str(e), 'error')
    except ProductAccessError as e:
        flash(str(e), 'error')
    except Exception:
        current_app.logger.exception(
            'Falha inesperada e não classificada ao revogar acesso a produto (org_id=%s, product_code=%s)',
            org_id, product_code,
        )
        flash('Não foi possível concluir a operação. Tente novamente.', 'error')
    return redirect(url_for('admin.org_details', org_id=org_id))
