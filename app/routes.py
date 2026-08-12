import logging
import urllib.parse

from flask import Blueprint, current_app, render_template

main_bp = Blueprint('main', __name__)

def is_valid_url(url, is_production=True):
    if not url or not isinstance(url, str):
        return False

    url = url.strip()
    if not url:
        return False

    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False

    # Verificar se é absoluta e tem hostname
    if not parsed.scheme or not parsed.netloc:
        return False

    # Validar o esquema
    allowed_schemes = ['https'] if is_production else ['http', 'https']
    if parsed.scheme.lower() not in allowed_schemes:
        return False

    return True

def get_available_systems():
    is_prod = current_app.config.get('IS_PRODUCTION', True)

    systems_config = [
        {
            "id": "l-kalender",
            "name": "L-Kalender",
            "description": "Sistema de calendário e planejamento estratégico para compras e licitações.",
            "url": current_app.config.get("L_KALENDER_URL"),
        },
        {
            "id": "l-gedo",
            "name": "L-GeDo",
            "description": "Sistema de gestão documental, mantendo seus documentos organizados e seguros.",
            "url": current_app.config.get("L_GEDO_URL"),
        }
    ]

    validated_systems = []

    for sys in systems_config:
        url = sys["url"]

        if is_valid_url(url, is_production=is_prod):
            sys["status"] = "available"
            sys["url"] = url.strip()
        else:
            if url: # Tem URL mas é inválida
                logging.warning(f"URL inválida configurada para o sistema {sys['id']}")
            sys["status"] = "unavailable"
            sys["url"] = None

        validated_systems.append(sys)

    return validated_systems

@main_bp.route('/')
def index():
    systems = get_available_systems()
    return render_template('index.html', systems=systems)
