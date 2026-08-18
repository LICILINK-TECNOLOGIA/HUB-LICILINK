from functools import wraps
from flask import flash, redirect, url_for
from flask_login import current_user

def internal_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not getattr(current_user, 'is_internal_admin', False):
            flash('Acesso negado. Esta área é restrita para administradores internos.', 'error')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function
