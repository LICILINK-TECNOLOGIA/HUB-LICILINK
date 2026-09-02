import hmac
import os
from flask import Blueprint, jsonify, request
from functools import wraps
from ..services.lead_service import LeadService
from ..extensions import csrf

api_bp = Blueprint('api', __name__)

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
    data = request.json
    if not data or 'idempotency_key' not in data or 'name' not in data or 'email' not in data:
        return jsonify({"error": "Invalid payload"}), 400
        
    lead, created = LeadService.process_lead(
        idempotency_key=data['idempotency_key'],
        name=data['name'],
        email=data['email'],
        phone=data.get('phone'),
        company=data.get('company'),
        source=data.get('source'),
        metadata_data=data.get('metadata_data')
    )
    
    if created:
        return jsonify({"message": "Lead processed successfully", "id": lead.id}), 201
    else:
        return jsonify({"message": "Lead already exists (idempotent)", "id": lead.id}), 200
