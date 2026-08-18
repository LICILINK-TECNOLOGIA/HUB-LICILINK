from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import login_user, logout_user, login_required
from ..services.auth_service import AuthService
from ..extensions import limiter

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        try:
            user = AuthService.authenticate(email, password)
            if user:
                login_user(user)
                return redirect(url_for('dashboard.index'))
            else:
                flash('Credenciais inválidas.', 'error')
        except ValueError as e:
            flash(str(e), 'error')
            
    return render_template('auth/login.html')

@auth_bp.route('/register', methods=['GET', 'POST'])
@limiter.limit("3 per minute", methods=["POST"])
def register():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        password_confirm = request.form.get('password_confirm')
        
        if password != password_confirm:
            flash('As senhas não coincidem.', 'error')
            return render_template('auth/register.html')
        
        try:
            pending = AuthService.start_registration(name, email, password)
            session['pending_registration_id'] = str(pending.id)
            return redirect(url_for('auth.verify'))
        except ValueError as e:
            flash(str(e), 'error')
        except Exception as e:
            flash('Ocorreu um erro inesperado.', 'error')
            
    return render_template('auth/register.html')

@auth_bp.route('/verify', methods=['GET', 'POST'])
@limiter.limit("5 per minute", methods=["POST"])
def verify():
    pending_id = session.get('pending_registration_id')
    if not pending_id:
        return redirect(url_for('auth.register'))
        
    if request.method == 'POST':
        code = request.form.get('code')
        try:
            user = AuthService.verify_email(pending_id, code)
            session.pop('pending_registration_id', None)
            login_user(user)
            flash('E-mail verificado com sucesso! Bem-vindo.', 'success')
            return redirect(url_for('dashboard.index'))
        except ValueError as e:
            flash(str(e), 'error')
            
    return render_template('auth/verify.html')

@auth_bp.route('/resend-code', methods=['POST'])
@limiter.limit("2 per minute", methods=["POST"])
def resend_code():
    pending_id = session.get('pending_registration_id')
    if not pending_id:
        return redirect(url_for('auth.register'))
        
    try:
        AuthService.resend_code(pending_id)
        flash('Um novo código foi enviado para o seu e-mail.', 'success')
    except ValueError as e:
        flash(str(e), 'error')
        
    return redirect(url_for('auth.verify'))

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))
