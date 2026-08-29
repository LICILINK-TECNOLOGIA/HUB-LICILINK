import os
from dotenv import load_dotenv
from flask import Flask

# Carrega as variáveis de ambiente antes de criar a aplicação
load_dotenv()
from .config import config_by_name, configure_secret_key
from .extensions import db, migrate, login_manager

def create_app(config_name=None):
    if config_name is None:
        config_name = os.getenv('FLASK_ENV', 'development')

    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name])

    # Valida/gera a SECRET_KEY assim que a configuração do ambiente é conhecida
    configure_secret_key(app)

    # Import Models so Alembic can detect them
    from . import models
    
    from .extensions import db, migrate, login_manager, limiter
    
    # Initialize Extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    limiter.init_app(app)
    
    # Initialize CLI commands
    from .cli import init_cli
    init_cli(app)
    
    login_manager.login_view = 'auth.login'
    login_manager.login_message = "Por favor, faça login para acessar esta página."
    
    @login_manager.user_loader
    def load_user(user_id):
        return models.User.query.get(user_id)
    
    # Import and Register Blueprints
    from .blueprints.health import health_bp
    from .blueprints.auth import auth_bp
    from .blueprints.dashboard import dashboard_bp
    from .blueprints.api import api_bp
    from .blueprints.admin import admin_bp
    
    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(api_bp, url_prefix='/api/v1')
    app.register_blueprint(admin_bp)
    
    return app
