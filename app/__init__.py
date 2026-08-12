from dotenv import load_dotenv
from flask import Flask

# Carrega as variáveis de ambiente antes de criar a aplicação
load_dotenv()

def create_app(config_class=None):
    app = Flask(__name__)

    if config_class is None:
        from app.config import Config
        app.config.from_object(Config)
    else:
        app.config.from_object(config_class)

    from app.routes import main_bp
    app.register_blueprint(main_bp)

    return app
