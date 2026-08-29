import os
import re
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

# Único driver PostgreSQL instalado no projeto (ver requirements.txt: psycopg/psycopg-binary).
# NÃO adicionar psycopg2 (ou qualquer outro driver) apenas para compatibilizar
# URLs que declarem um dialeto diferente do instalado.
POSTGRES_DIALECT_PREFIX = 'postgresql+psycopg://'

# Reconhece `postgres://`, `postgresql://` (bare) e `postgresql+<driver>://`,
# capturando o driver explícito (se houver) no grupo 2.
_POSTGRES_SCHEME_PATTERN = re.compile(r'^(postgres(?:ql)?)(\+[A-Za-z0-9_]+)?://')

# Mesmas credenciais de desenvolvimento local já usadas em docker-compose.dev.yml.
# O compose só define o serviço `db` (PostgreSQL) — não há serviço/Dockerfile
# para o Flask, então o cenário suportado é: Flask no host, PostgreSQL no
# Docker, acessado pela porta publicada no host (5433, ver docker-compose.dev.yml).
DEFAULT_DEVELOPMENT_DATABASE_URI = 'postgresql+psycopg://hub_user:hub_password@localhost:5433/hub_db'


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
    # SQLALCHEMY_DATABASE_URI é resolvida dinamicamente por configure_database_uri()
    # em create_app(), para não congelar DATABASE_URL no momento da importação.

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
    # SQLALCHEMY_DATABASE_URI é resolvida dinamicamente por configure_database_uri()

class ProductionConfig(Config):
    DEBUG = False
    IS_PRODUCTION = True
    SESSION_COOKIE_SECURE = True
    # SQLALCHEMY_DATABASE_URI é resolvida dinamicamente por configure_database_uri()

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


def _normalize_postgres_dialect(database_url):
    """Normaliza o dialeto de uma URL PostgreSQL para o único driver instalado
    no projeto (`psycopg` v3), usando uma política de allowlist estrita:

    - `postgres://...` e `postgresql://...` (sem driver explícito) são
      reescritas para `postgresql+psycopg://...`;
    - `postgresql+psycopg://...` é preservada sem alteração;
    - qualquer outro driver PostgreSQL explícito (`postgresql+psycopg2://`,
      `postgresql+asyncpg://` etc.) é REJEITADO com `RuntimeError` antes que
      o SQLAlchemy tente importar esse driver;
    - URLs que não são PostgreSQL (ex.: `sqlite://`) são respeitadas sem
      alteração.

    A mensagem de erro nunca inclui a URI, usuário, senha, host ou nome do
    banco — apenas o nome do dialeto rejeitado (que não é uma credencial).
    """
    if not database_url:
        return database_url

    match = _POSTGRES_SCHEME_PATTERN.match(database_url)
    if not match:
        return database_url  # não é uma URL PostgreSQL (ex.: sqlite)

    explicit_driver = match.group(2)  # None, ou '+psycopg', '+psycopg2', '+asyncpg', ...

    if explicit_driver is None:
        # postgres:// ou postgresql:// sem driver explícito: normaliza.
        rest = database_url[match.end():]
        return POSTGRES_DIALECT_PREFIX + rest

    if explicit_driver == '+psycopg':
        return database_url  # já é o único driver instalado

    dialect_name = explicit_driver.lstrip('+')
    raise RuntimeError(
        f"SQLALCHEMY_DATABASE_URI usa o dialeto PostgreSQL '{dialect_name}', que "
        "não está instalado neste projeto (apenas o driver 'psycopg' v3 está "
        "disponível). Use 'postgresql+psycopg://' explicitamente, ou omita o "
        "driver na URL (será normalizado automaticamente)."
    )


def configure_database_uri(app):
    """Resolve SQLALCHEMY_DATABASE_URI a partir do ambiente atual, no momento
    da criação da aplicação (não em tempo de importação de `app.config`).

    Config classes que já definem a URI explicitamente (ex.: TestingConfig,
    com SQLite) não são afetadas. Em development sem `DATABASE_URL` externa,
    usa o padrão local já documentado (Flask no host, PostgreSQL no Docker).
    Em staging/produção, `DATABASE_URL` é obrigatória — a aplicação falha
    explicitamente e antes de qualquer inicialização do SQLAlchemy caso
    esteja ausente, sem nunca expor a URI/credenciais na mensagem de erro.
    """
    if app.config.get('SQLALCHEMY_DATABASE_URI'):
        return

    database_url = os.getenv('DATABASE_URL')

    if app.config.get('IS_PRODUCTION'):
        if not database_url:
            raise RuntimeError(
                "DATABASE_URL ausente. Defina a variável de ambiente DATABASE_URL "
                "com a string de conexão do PostgreSQL (dialeto 'postgresql+psycopg://') "
                "antes de iniciar a aplicação neste ambiente."
            )
    elif not database_url:
        database_url = DEFAULT_DEVELOPMENT_DATABASE_URI

    app.config['SQLALCHEMY_DATABASE_URI'] = _normalize_postgres_dialect(database_url)
