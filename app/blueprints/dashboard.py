from flask import Blueprint, render_template
from flask_login import login_required, current_user
from ..services.organization_service import OrganizationService
from ..services.access_service import AccessService

dashboard_bp = Blueprint('dashboard', __name__)

@dashboard_bp.route('/')
@login_required
def index():
    # Obtém as organizações do usuário
    orgs = OrganizationService.get_user_organizations(current_user.id)
    
    # Se ele for de mais de uma organização, pegaríamos da sessão (Tenant).
    # Para a V1 simplificada, vamos assumir que o usuário opera na primeira
    # organização que ele pertence.
    current_org = orgs[0] if orgs else None
    
    if current_org:
        launcher_items = AccessService.get_organization_products(current_user.id, current_org.id)
    else:
        launcher_items = []
        
    return render_template('dashboard/launcher.html', org=current_org, items=launcher_items)
