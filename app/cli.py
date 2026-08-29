import click
from flask.cli import with_appcontext
from .extensions import db
from .models.identity import User, PendingEmailVerification
from .services.audit_service import AuditService
from datetime import datetime

# Comprimento mínimo exigido para senhas de administrador provisionadas via CLI.
MIN_ADMIN_PASSWORD_LENGTH = 12


def _validate_admin_password(password):
    """Valida a senha antes de qualquer alteração no banco.

    Nunca inclui o valor da senha na mensagem de erro.
    """
    if not password or not password.strip():
        raise click.UsageError("Senha inválida: não pode ser vazia.")
    if len(password) < MIN_ADMIN_PASSWORD_LENGTH:
        raise click.UsageError(
            f"Senha inválida: deve conter ao menos {MIN_ADMIN_PASSWORD_LENGTH} caracteres."
        )


def _prompt_admin_password(prompt_text):
    """Solicita a senha por prompt oculto, com confirmação obrigatória.

    Nunca aceita a senha como argumento de linha de comando (não existe
    `--password` nesta CLI): evita que ela apareça no histórico do shell ou
    na lista de processos. Em execução não interativa (sem entrada
    disponível), o prompt falha com `click.Abort`, antes de qualquer
    alteração no banco.
    """
    password = click.prompt(
        prompt_text,
        hide_input=True,
        confirmation_prompt="Confirme a senha",
    )
    _validate_admin_password(password)
    return password


@click.command("create-admin")
@click.option("--name", required=True)
@click.option("--email", required=True)
@with_appcontext
def create_admin_command(name, email):
    """Cria um novo administrador interno (Internal Admin) do HUB LiciLink.

    A senha é solicitada de forma interativa (prompt oculto, com
    confirmação) — nunca via argumento de linha de comando. Não redefine a
    senha de um administrador já existente com o mesmo e-mail; use
    `reset-admin-password` para isso, de forma explícita e separada.
    """
    normalized_email = email.strip().lower()

    existing_user = User.query.filter_by(email=normalized_email).first()
    if existing_user:
        click.echo(f"Erro: o e-mail {normalized_email} já está em uso.")
        return

    password = _prompt_admin_password("Senha do novo administrador")

    admin_user = User(
        name=name,
        email=normalized_email,
        is_internal_admin=True,
    )
    admin_user.set_password(password)
    try:
        db.session.add(admin_user)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise click.ClickException(
            "Erro ao criar administrador. Nenhuma alteração foi salva."
        )

    AuditService.log_action('admin_user_created', user_id=admin_user.id)
    click.echo(f"Administrador interno {name} ({normalized_email}) criado com sucesso!")


@click.command("reset-admin-password")
@click.option("--email", required=True)
@click.confirmation_option(
    "--yes",
    prompt="Tem certeza que deseja redefinir a senha deste administrador?",
)
@with_appcontext
def reset_admin_password_command(email):
    """Redefine a senha de um administrador interno já existente, identificado
    explicitamente por e-mail. A senha é solicitada de forma interativa
    (prompt oculto, com confirmação) — nunca via argumento de linha de
    comando. Exige confirmação explícita da ação (--yes ou prompt
    interativo) e nunca cria um novo administrador."""
    normalized_email = email.strip().lower()

    admin = User.query.filter_by(email=normalized_email, is_internal_admin=True).first()
    if not admin:
        click.echo("Erro: nenhum administrador interno encontrado com este e-mail.")
        return

    password = _prompt_admin_password("Nova senha do administrador")

    admin.set_password(password)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise click.ClickException(
            "Erro ao redefinir a senha. Nenhuma alteração foi salva."
        )

    AuditService.log_action('admin_password_reset', user_id=admin.id)
    click.echo(f"Senha do administrador {normalized_email} redefinida com sucesso.")


@click.command("cleanup-expired-verifications")
@with_appcontext
def cleanup_expired_verifications_command():
    """Remove do banco de dados as verificações de e-mail que já expiraram."""
    now = datetime.utcnow()
    expired_records = PendingEmailVerification.query.filter(PendingEmailVerification.expires_at < now).all()
    count = len(expired_records)

    if count > 0:
        for record in expired_records:
            db.session.delete(record)
        db.session.commit()

        # Log de sistema (sem user_id, ação automática)
        AuditService.log_action('system.cleanup.expired_verifications', details={'count': count})
        click.echo(f"Limpeza concluída: {count} registros expirados removidos.")
    else:
        click.echo("Nenhum registro expirado encontrado para limpeza.")

def init_cli(app):
    app.cli.add_command(create_admin_command)
    app.cli.add_command(reset_admin_password_command)
    app.cli.add_command(cleanup_expired_verifications_command)
