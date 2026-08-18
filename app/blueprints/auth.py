from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required
from ..services.auth_service import AuthService

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        user = AuthService.authenticate(email, password)
        if user:
            login_user(user)
            return redirect(url_for('dashboard.index'))
        else:
            flash('Credenciais inválidas.', 'error')
            
    return render_template('auth/login.html')

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        
        try:
            user = AuthService.register_user(name, email, password)
            login_user(user)
            flash('Conta criada com sucesso!', 'success')
            return redirect(url_for('dashboard.index'))
        except ValueError as e:
            flash(str(e), 'error')
        except Exception as e:
            flash('Ocorreu um erro inesperado.', 'error')
            
    return render_template('auth/register.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))
