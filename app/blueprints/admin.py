from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required
from ..decorators import internal_admin_required
from ..models.identity import User, Organization
from ..models.crm import Lead
from ..extensions import db

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

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

from ..services.organization_service import OrganizationService
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
    except Exception as e:
        flash(f'Erro ao criar organização: {str(e)}', 'error')
        return redirect(url_for('admin.new_organization'))

@admin_bp.route('/organizations/<uuid:org_id>', methods=['GET'])
def org_details(org_id):
    org = Organization.query.get_or_404(org_id)
    roles = Role.query.all()
    users = User.query.all()
    return render_template('admin/org_details.html', org=org, roles=roles, users=users)

@admin_bp.route('/organizations/<uuid:org_id>/members', methods=['POST'])
def add_member(org_id):
    user_id = request.form.get('user_id')
    role_name = request.form.get('role')
    try:
        OrganizationService.add_member(org_id, user_id, role_name)
        flash('Membro adicionado com sucesso.', 'success')
    except Exception as e:
        flash(f'Erro: {str(e)}', 'error')
    return redirect(url_for('admin.org_details', org_id=org_id))

@admin_bp.route('/organizations/<uuid:org_id>/members/<uuid:user_id>/role', methods=['POST'])
def change_member_role(org_id, user_id):
    role_name = request.form.get('role')
    try:
        OrganizationService.change_member_role(org_id, user_id, role_name)
        flash('Papel alterado com sucesso.', 'success')
    except Exception as e:
        flash(f'Erro: {str(e)}', 'error')
    return redirect(url_for('admin.org_details', org_id=org_id))

@admin_bp.route('/organizations/<uuid:org_id>/members/<uuid:user_id>/remove', methods=['POST'])
def remove_member(org_id, user_id):
    try:
        OrganizationService.remove_member(org_id, user_id)
        flash('Membro removido com sucesso.', 'success')
    except Exception as e:
        flash(f'Erro ao remover: {str(e)}', 'error')
    return redirect(url_for('admin.org_details', org_id=org_id))

@admin_bp.route('/users/<uuid:user_id>/organization', methods=['POST'])
def link_user_to_org(user_id):
    org_id = request.form.get('organization_id')
    role_name = request.form.get('role', 'owner')
    try:
        OrganizationService.add_member(org_id, user_id, role_name)
        flash('Usuário vinculado com sucesso.', 'success')
    except Exception as e:
        flash(f'Erro: {str(e)}', 'error')
    return redirect(url_for('admin.users_orgs'))
