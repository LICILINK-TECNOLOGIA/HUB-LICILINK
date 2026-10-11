"""Entrada de implantação do HUB (staging e produção).

`create_app()` continua sendo a factory única e não muda: ela aceita
'development' quando `FLASK_ENV` está ausente e usa valores padrão embutidos
para várias configurações, o que é conveniente no desenvolvimento local e
errado numa implantação. Este módulo é a fronteira de implantação, usada pelo
servidor WSGI (`app/wsgi.py`) e, dentro da imagem, também pelo comando
`flask` (via `FLASK_APP`):

- exige `FLASK_ENV` presente e igual a `staging` ou `production` - nunca cai
  em 'development';
- exige, sem valor padrão, as variáveis que numa implantação não podem ser
  implícitas: quantidade de proxies confiáveis, provedor de e-mail e URLs dos
  produtos;
- aplica `ProxyFix` somente com a quantidade declarada, e só para
  `X-Forwarded-For` e `X-Forwarded-Proto`.

Toda mensagem de erro cita apenas o NOME da variável e a regra - nunca o
valor recebido.
"""
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from werkzeug.middleware.proxy_fix import ProxyFix

from . import create_app

DEPLOYED_ENVIRONMENTS = ('staging', 'production')

# Quantos proxies confiáveis existem entre o cliente e o HUB, para cada
# cabeçalho. Configurados separadamente; 0 desliga a leitura daquele cabeçalho.
PROXY_FIX_X_FOR_VAR = 'HUB_PROXY_FIX_X_FOR'
PROXY_FIX_X_PROTO_VAR = 'HUB_PROXY_FIX_X_PROTO'
PROXY_FIX_MAX_TRUSTED = 5

PRODUCT_URL_VARS = ('L_KALENDER_URL', 'L_GEDO_URL', 'L_HUNT_URL')

EMAIL_PROVIDER_VAR = 'EMAIL_PROVIDER'
EMAIL_PROVIDERS = ('console', 'resend')
# Exigidas quando EMAIL_PROVIDER=resend (ver app/services/email/providers/resend.py).
RESEND_REQUIRED_VARS = ('RESEND_API_KEY', 'EMAIL_FROM')


@dataclass(frozen=True)
class DeploymentSettings:
    x_for: int
    x_proto: int
    email_provider: str
    product_urls: dict
    resend: dict


def resolve_deployed_environment():
    """Devolve 'staging' ou 'production' a partir de `FLASK_ENV`. Ausente,
    vazia ou qualquer outro valor (inclusive 'development' e 'testing')
    levanta `RuntimeError`: o servidor WSGI nunca sobe em outro ambiente."""
    normalized = (os.environ.get('FLASK_ENV') or '').strip().lower()
    if normalized not in DEPLOYED_ENVIRONMENTS:
        allowed = ' ou '.join(DEPLOYED_ENVIRONMENTS)
        raise RuntimeError(
            f"FLASK_ENV ausente ou inválida para o servidor WSGI. Defina FLASK_ENV como {allowed}."
        )
    return normalized


def parse_trusted_proxy_count(raw_value, var_name):
    """Quantidade de proxies confiáveis: inteiro de 0 a `PROXY_FIX_MAX_TRUSTED`,
    declarado explicitamente. Ausente, vazia, não numérica ou fora da faixa
    levanta `RuntimeError` com mensagem fixa."""
    text = (raw_value or '').strip()
    if not text:
        raise RuntimeError(
            f"{var_name} ausente. Declare explicitamente um inteiro entre 0 e "
            f"{PROXY_FIX_MAX_TRUSTED} (0 desliga a leitura do cabeçalho)."
        )
    # O limite de tamanho vem antes de `int()`: nada além de um dígito é válido.
    if len(text) != 1 or not (text.isascii() and text.isdigit()) or int(text) > PROXY_FIX_MAX_TRUSTED:
        raise RuntimeError(
            f"{var_name} inválida: deve ser um inteiro entre 0 e {PROXY_FIX_MAX_TRUSTED}."
        )
    return int(text)


def _product_url_problem(raw_value):
    text = (raw_value or '').strip()
    if not text:
        return "ausente"
    try:
        parts = urlsplit(text)
        hostname = parts.hostname
        has_credentials = parts.username is not None or parts.password is not None
    except ValueError:
        return "inválida"
    if parts.scheme != 'https' or not hostname or has_credentials:
        return "inválida: deve ser uma URL https com host e sem credenciais"
    return None


def load_deployment_settings():
    """Lê e valida, do ambiente do processo, tudo o que uma implantação
    precisa declarar. Reúne todos os problemas numa única `RuntimeError`,
    citando só nomes de variável."""
    env = os.environ
    problems = []

    counts = {}
    for var_name in (PROXY_FIX_X_FOR_VAR, PROXY_FIX_X_PROTO_VAR):
        try:
            counts[var_name] = parse_trusted_proxy_count(env.get(var_name), var_name)
        except RuntimeError as exc:
            problems.append(str(exc))

    email_provider = (env.get(EMAIL_PROVIDER_VAR) or '').strip().lower()
    if not email_provider:
        problems.append(
            f"{EMAIL_PROVIDER_VAR} ausente. Declare explicitamente: {', '.join(EMAIL_PROVIDERS)}."
        )
    elif email_provider not in EMAIL_PROVIDERS:
        problems.append(
            f"{EMAIL_PROVIDER_VAR} inválida: deve ser um destes valores: {', '.join(EMAIL_PROVIDERS)}."
        )

    resend = {}
    if email_provider == 'resend':
        for var_name in RESEND_REQUIRED_VARS:
            value = (env.get(var_name) or '').strip()
            if not value:
                problems.append(f"{var_name} ausente: obrigatória com {EMAIL_PROVIDER_VAR}=resend.")
            resend[var_name] = value

    product_urls = {}
    for var_name in PRODUCT_URL_VARS:
        problem = _product_url_problem(env.get(var_name))
        if problem:
            problems.append(f"{var_name} {problem}.")
        else:
            product_urls[var_name] = env[var_name].strip()

    if problems:
        raise RuntimeError("Configuração de implantação inválida. " + " ".join(problems))

    return DeploymentSettings(
        x_for=counts[PROXY_FIX_X_FOR_VAR],
        x_proto=counts[PROXY_FIX_X_PROTO_VAR],
        email_provider=email_provider,
        product_urls=product_urls,
        resend=resend,
    )


def apply_proxy_fix(app, *, x_for, x_proto):
    """Confia em exatamente `x_for` valores de `X-Forwarded-For` e `x_proto`
    de `X-Forwarded-Proto`, contados a partir do proxy mais próximo. Com os
    dois em 0 o middleware nem é instalado. `X-Forwarded-Host`, `-Port` e
    `-Prefix` nunca são lidos."""
    for name, value in (('x_for', x_for), ('x_proto', x_proto)):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= PROXY_FIX_MAX_TRUSTED:
            raise ValueError(f"{name} deve ser um inteiro entre 0 e {PROXY_FIX_MAX_TRUSTED}.")
    app.config['PROXY_FIX_X_FOR'] = x_for
    app.config['PROXY_FIX_X_PROTO'] = x_proto
    if x_for or x_proto:
        app.wsgi_app = ProxyFix(
            app.wsgi_app, x_for=x_for, x_proto=x_proto, x_host=0, x_port=0, x_prefix=0,
        )
    return app


def _warn_about_local_only_settings(app, settings):
    if settings.x_for == 0:
        app.logger.warning(
            "%s=0: X-Forwarded-For é ignorado. Atrás de um proxy, todos os usuários "
            "dividem o mesmo limite de login e a auditoria grava o endereço do proxy. "
            "Aceitável só na validação local da imagem.",
            PROXY_FIX_X_FOR_VAR,
        )
    if settings.x_proto == 0:
        app.logger.warning(
            "%s=0: X-Forwarded-Proto é ignorado. Atrás de um proxy que termina o TLS, "
            "o HUB enxerga as requisições como HTTP. Aceitável só na validação local "
            "da imagem.",
            PROXY_FIX_X_PROTO_VAR,
        )
    if settings.email_provider == 'console':
        app.logger.warning(
            "%s=console: cada e-mail é impresso inteiro na saída do processo, com o "
            "código de verificação. Aceitável só na validação local, com dados sintéticos.",
            EMAIL_PROVIDER_VAR,
        )


def create_deployed_app():
    """Cria a aplicação para staging ou produção. Qualquer exigência não
    atendida impede a criação, antes de qualquer extensão ser inicializada."""
    config_name = resolve_deployed_environment()
    settings = load_deployment_settings()

    app = create_app(config_name)

    # Os valores validados acima passam a valer explicitamente: nenhuma URL de
    # produto nem o provedor de e-mail dependem do padrão embutido em Config.
    app.config.update(settings.product_urls)
    app.config[EMAIL_PROVIDER_VAR] = settings.email_provider
    app.config.update(settings.resend)

    apply_proxy_fix(app, x_for=settings.x_for, x_proto=settings.x_proto)
    _warn_about_local_only_settings(app, settings)
    return app
