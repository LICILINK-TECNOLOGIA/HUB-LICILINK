import uuid

from dotenv import load_dotenv
from flask import Flask

# Carrega as variáveis de ambiente antes de criar a aplicação
load_dotenv()
from .config import config_by_name, resolve_config_name, configure_secret_key, configure_database_uri
from .extensions import db, migrate, login_manager

def create_app(config_name=None):
    config_name = resolve_config_name(config_name)

    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name])

    # Valida/gera a SECRET_KEY assim que a configuração do ambiente é conhecida
    configure_secret_key(app)
    # Resolve a URI do banco (driver explícito, sem congelamento por import)
    configure_database_uri(app)

    # Import Models so Alembic can detect them
    from . import models
    
    from .extensions import db, migrate, login_manager, limiter, csrf

    # Initialize Extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    limiter.init_app(app)
    csrf.init_app(app)

    # Initialize CLI commands
    from .cli import init_cli
    init_cli(app)

    # Initialize error handlers (ex.: CSRFError)
    from .errors import register_error_handlers
    register_error_handlers(app)
    
    login_manager.login_view = 'auth.login'
    login_manager.login_message = "Por favor, faça login para acessar esta página."
    
    @login_manager.user_loader
    def load_user(user_id):
        # Flask-Login sempre entrega `user_id` como `str` (via
        # `UserMixin.get_id()`), mas a PK real e' `UUID(as_uuid=True)`. O
        # bind processor do SQLite (sem suporte nativo a UUID) exige um
        # `uuid.UUID` de verdade e quebra com AttributeError ao receber uma
        # `str` crua - por isso o gate de tipo abaixo, antes de qualquer
        # tentativa de conversao (um int cuja representacao textual seja um
        # hexadecimal de 32 digitos passaria por `uuid.UUID(str(...))` sem
        # erro, entao a rejeicao nao pode depender só disso). Ver Issue #69.
        if not isinstance(user_id, (str, uuid.UUID)):
            return None
        try:
            normalized_user_id = uuid.UUID(str(user_id))
        except Exception:
            return None
        return db.session.get(models.User, normalized_user_id)
    
    # Import and Register Blueprints
    from .blueprints.health import health_bp
    from .blueprints.auth import auth_bp
    from .blueprints.dashboard import dashboard_bp
    from .blueprints.api import api_bp
    from .blueprints.admin import admin_bp
    from .blueprints.federation import federation_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(api_bp, url_prefix='/api/v1')
    app.register_blueprint(admin_bp)
    app.register_blueprint(federation_bp)
    
    return app
