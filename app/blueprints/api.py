import hmac
import os
from flask import Blueprint, current_app, jsonify, request
from functools import wraps
from ..services.lead_service import LEAD_OPERATION_ERROR_MESSAGE, LeadService, LeadOperationError
from ..extensions import csrf, db

api_bp = Blueprint('api', __name__)

_INVALID_PAYLOAD_RESPONSE = {"error": "Invalid payload"}
_LEAD_OPERATION_ERROR_RESPONSE = {"error": "Não foi possível processar o lead. Tente novamente."}


def _required_string(data, field_name):
    """Retorna o valor de `field_name` somente se for uma string não
    vazia e não composta só de espaços (Issue #47) - nunca normaliza nem
    substitui o valor original; `strip()` é usado apenas para decidir se
    a string está vazia, o valor original (sem strip) é o que segue para
    o service. Retorna `None` para valor ausente, `null`, não-string, ou
    string vazia/só espaços - qualquer um desses casos é payload
    inválido."""
    value = data.get(field_name)
    if not isinstance(value, str) or not value.strip():
        return None
    return value

def require_api_key(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({"error": "Missing or invalid token"}), 401
            
        token = auth_header.split(' ')[1]
        expected_key = os.getenv('HUB_API_KEY')

        # Comparação de tempo constante (Issue #43, CWE-208): HUB_API_KEY
        # ausente/vazia nunca autentica nenhum token, mesmo vazio - a
        # checagem `not expected_key` decide isso ANTES de qualquer
        # chamada a compare_digest, então este nunca recebe None. Os dois
        # lados são explicitamente codificados para bytes (mesmo tipo),
        # o que também torna caracteres não-ASCII no token seguros (nunca
        # gera TypeError) - eles só resultam em bytes diferentes, e
        # portanto em rejeição.
        if not expected_key or not hmac.compare_digest(
            token.encode('utf-8'),
            expected_key.encode('utf-8'),
        ):
            return jsonify({"error": "Unauthorized"}), 403
            
        return f(*args, **kwargs)
    return decorated_function

@api_bp.route('/leads', methods=['POST'])
@csrf.exempt  # Autenticado por Authorization: Bearer HUB_API_KEY, não por cookie de sessão - CSRF não se aplica.
@require_api_key
def create_lead():
    # silent=True: corpo ausente, Content-Type incorreto ou JSON
    # malformado viram None (sem levantar a exceção HTML padrão do
    # Werkzeug) - tratados uniformemente como payload inválido abaixo,
    # junto com JSON válido que não seja um objeto (Issue #47).
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(_INVALID_PAYLOAD_RESPONSE), 400

    idempotency_key = _required_string(data, 'idempotency_key')
    name = _required_string(data, 'name')
    email = _required_string(data, 'email')
    if idempotency_key is None or name is None or email is None:
        return jsonify(_INVALID_PAYLOAD_RESPONSE), 400

    try:
        lead, created = LeadService.process_lead(
            idempotency_key=idempotency_key,
            name=name,
            email=email,
            phone=data.get('phone'),
            company=data.get('company'),
            source=data.get('source'),
            metadata_data=data.get('metadata_data')
        )
        if created:
            return jsonify({"message": "Lead processed successfully", "id": lead.id}), 201
        else:
            return jsonify({"message": "Lead already exists (idempotent)", "id": lead.id}), 200
    except LeadOperationError:
        current_app.logger.exception('Falha inesperada ao processar lead')
        # A fronteira HTTP não confia no texto da exceção:
        # `LeadOperationError` continua sendo um `ValueError` que aceitaria
        # qualquer texto, então a resposta usa sempre a constante pública
        # curada, nunca `str(exc)` (Issue #47).
        return jsonify({"error": LEAD_OPERATION_ERROR_MESSAGE}), 500
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Falha inesperada e não classificada ao processar lead')
        return jsonify(_LEAD_OPERATION_ERROR_RESPONSE), 500
