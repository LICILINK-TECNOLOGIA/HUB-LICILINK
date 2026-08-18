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
    return render_template('admin/users_orgs.html', users=users, orgs=orgs)

@admin_bp.route('/crm')
def crm_leads():
    leads = Lead.query.order_by(Lead.created_at.desc()).all()
    return render_template('admin/crm_leads.html', leads=leads)
