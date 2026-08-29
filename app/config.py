import os
import secrets
from dotenv import load_dotenv

load_dotenv()

# Comprimento mínimo aceito para SECRET_KEY em staging/produção.
MIN_SECRET_KEY_LENGTH = 32

# Valores conhecidos que nunca devem ser aceitos como SECRET_KEY fora de development/testing.
KNOWN_INSECURE_SECRET_KEYS = {
    'default-secret-key',
    'change-me-in-development',
}


class Config:
    """Base config."""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    HUB_API_KEY = os.getenv('HUB_API_KEY')

    IS_PRODUCTION = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    L_KALENDER_URL = os.getenv('L_KALENDER_URL', 'https://kalender-hml.licilink.com.br')
    L_GEDO_URL = os.getenv('L_GEDO_URL', 'https://gedo-hml.licilink.com.br')
    L_HUNT_URL = os.getenv('L_HUNT_URL', 'https://hunt-hml.licilink.com.br')

    # Configurações de E-mail Verification
    VERIFICATION_CODE_TTL = int(os.getenv('VERIFICATION_CODE_TTL', 600))
    VERIFICATION_MAX_ATTEMPTS = int(os.getenv('VERIFICATION_MAX_ATTEMPTS', 5))
    VERIFICATION_RESEND_COOLDOWN = int(os.getenv('VERIFICATION_RESEND_COOLDOWN', 60))
    VERIFICATION_MAX_RESENDS = int(os.getenv('VERIFICATION_MAX_RESENDS', 5))
    EMAIL_PROVIDER = os.getenv('EMAIL_PROVIDER', 'console')
    RESEND_API_KEY = os.getenv('RESEND_API_KEY')
    EMAIL_FROM = os.getenv('EMAIL_FROM')
    EMAIL_REPLY_TO = os.getenv('EMAIL_REPLY_TO')
class DevelopmentConfig(Config):
    DEBUG = True
    IS_PRODUCTION = False
    SESSION_COOKIE_SECURE = False
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'postgresql://hub_user:hub_password@localhost:5432/hub_db')

class TestingConfig(Config):
    TESTING = True
    IS_PRODUCTION = False
    SESSION_COOKIE_SECURE = False
    # Chave fixa exclusiva de teste: nunca lida do ambiente, nunca usada fora da suíte de testes.
    SECRET_KEY = 'testing-only-secret-key-do-not-use-outside-tests'
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:' # SQLite apenas para testes automatizados unitários se necessário

class StagingConfig(Config):
    DEBUG = False
    IS_PRODUCTION = True
    SESSION_COOKIE_SECURE = True
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL')

class ProductionConfig(Config):
    DEBUG = False
    IS_PRODUCTION = True
    SESSION_COOKIE_SECURE = True
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL')

config_by_name = dict(
    development=DevelopmentConfig,
    testing=TestingConfig,
    staging=StagingConfig,
    production=ProductionConfig
)


def configure_secret_key(app):
    """Valida ou gera a SECRET_KEY depois que a configuração da app foi carregada.

    Em staging/produção (IS_PRODUCTION=True), a SECRET_KEY deve vir de uma
    variável de ambiente válida; a aplicação falha explicitamente caso
    contrário. Em development, quando ausente, gera uma chave temporária
    somente em memória (nunca persistida em arquivo ou log). TestingConfig
    já define sua própria chave fixa e não passa pela validação/geração
    abaixo, pois `app.config['SECRET_KEY']` já estará preenchido.
    """
    if app.config.get('SECRET_KEY'):
        return

    secret_key = os.getenv('SECRET_KEY')

    if app.config.get('IS_PRODUCTION'):
        if not secret_key:
            raise RuntimeError(
                "SECRET_KEY ausente. Defina a variável de ambiente SECRET_KEY "
                "com uma chave forte e exclusiva antes de iniciar a aplicação "
                "neste ambiente."
            )
        if secret_key in KNOWN_INSECURE_SECRET_KEYS:
            raise RuntimeError(
                "SECRET_KEY inválida: corresponde a um valor de exemplo/placeholder "
                "conhecido. Defina uma chave forte e exclusiva via variável de ambiente."
            )
        if len(secret_key) < MIN_SECRET_KEY_LENGTH:
            raise RuntimeError(
                f"SECRET_KEY inválida: deve conter ao menos {MIN_SECRET_KEY_LENGTH} "
                "caracteres. Defina uma chave forte via variável de ambiente."
            )
        app.config['SECRET_KEY'] = secret_key
    else:
        # Development (ou qualquer ambiente não-produtivo sem chave própria):
        # gera uma chave temporária somente em memória, nunca persistida.
        app.config['SECRET_KEY'] = secret_key or secrets.token_hex(32)
