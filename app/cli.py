import click
from flask.cli import with_appcontext
from werkzeug.security import generate_password_hash
from .extensions import db
from .models.identity import User
from .services.audit_service import AuditService

@click.command("create-admin")
@click.option("--name", required=True)
@click.option("--email", required=True)
@click.option("--password", required=True)
@with_appcontext
def create_admin_command(name, email, password):
    """Cria um usuário administrador interno (Internal Admin) do HUB LiciLink"""
    user = User.query.filter_by(email=email).first()
    if user:
        click.echo(f"Erro: O e-mail {email} já está em uso.")
        return
    
    password_hash = generate_password_hash(password)
    admin_user = User(
        name=name,
        email=email,
        password_hash=password_hash,
        is_internal_admin=True
    )
    db.session.add(admin_user)
    db.session.commit()
    
    AuditService.log_action('admin_user_created', user_id=admin_user.id)
    click.echo(f"Administrador interno {name} ({email}) criado com sucesso!")

def init_cli(app):
    app.cli.add_command(create_admin_command)
