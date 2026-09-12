import dataclasses

from flask import Blueprint, current_app, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from ..extensions import csrf, db
from ..services.product_launch_code_service import (
    ProductLaunchCodeError,
    ProductLaunchCodeOperationError,
    ProductLaunchCodeService,
)

federation_bp = Blueprint('federation', __name__, url_prefix='/api/federation')

# Issue #67: teto de tamanho do corpo, verificado antes de qualquer
# parsing - nenhum `MAX_CONTENT_LENGTH` global existe no projeto hoje;
# este limite é aplicado somente a esta rota.
_MAX_BODY_BYTES = 4096
_MAX_PRESENTED_CODE_LENGTH = 256

# Corpos de erro estáveis e genéricos (Issue #67) - nunca a mensagem
# curada de `ProductLaunchCodeError`/`ProductLaunchCodeOperationError`
# (que, mesmo já sendo segura, não faz parte do contrato público
# versionado desta rota) e nunca `str(exc)` cru.
_INVALID_REQUEST_RESPONSE = {"error": "invalid_request"}
_INVALID_CREDENTIALS_OR_CODE_RESPONSE = {"error": "invalid_credentials_or_code"}
_INTERNAL_ERROR_RESPONSE = {"error": "internal_error"}


def _no_store_response(body, status):
    """Toda resposta desta rota (sucesso e erro) carrega `Cache-Control:
    no-store` + `Pragma: no-cache` - a de sucesso contém identidade, as
    de erro não devem ser cacheadas por nenhum intermediário."""
    response = jsonify(body)
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Pragma'] = 'no-cache'
    return response


@federation_bp.route(
    '/launch/consume',
    # Achado M1-3 da revisão técnica: GET/PUT/PATCH/DELETE são
    # REGISTRADOS aqui (nunca deixados de fora) para que caiam DENTRO
    # desta view - um 405 gerado pelo roteamento do Werkzeug (método
    # fora da lista) nunca passa por `@federation_bp.errorhandler(...)`
    # (comprovado empiricamente: o handler de blueprint nunca é
    # chamado para esse caso, já que o endpoint não é resolvido antes
    # do erro de roteamento). Registrando os métodos e checando
    # `request.method` no corpo da própria função, o 405 sai com o
    # MESMO contrato JSON/cache das demais respostas desta rota, sem
    # tocar nenhum handler global (que afetaria outras rotas) nem
    # exigir um errorhandler que nunca dispararia.
    methods=['POST', 'GET', 'PUT', 'PATCH', 'DELETE'],
)
# Autenticado por Authorization: Basic (credencial da instalação, #64),
# não por cookie de sessão - CSRF não se aplica. Isenta somente esta
# função; nenhuma outra rota do HUB perde proteção CSRF.
@csrf.exempt
def consume_launch_code():
    if request.method != 'POST':
        # Endpoint funcionalmente somente-POST: nenhuma leitura de
        # corpo, nenhuma checagem de Basic Auth, nenhuma chamada ao
        # service, nenhum acesso ao banco para qualquer outro método.
        return _no_store_response(_INVALID_REQUEST_RESPONSE, 405)

    # Achado A2-1 da revisão técnica: o teto de leitura do WSGI stream
    # em si (Flask/Werkzeug >= 3.1, `Request.max_content_length`
    # settável por instância) - ao contrário de só inspecionar
    # `Content-Length` (declarado pelo cliente e por isso contornável
    # por um corpo sem esse header, ex.: `Transfer-Encoding: chunked`),
    # isto limita os bytes que `get_input_stream`/`LimitedStream`
    # efetivamente entregam à leitura real do corpo, não importa o que
    # o cliente declare ou omita. Aplicado só nesta requisição (nunca
    # `current_app.config['MAX_CONTENT_LENGTH']`, que valeria para toda
    # a aplicação) e ANTES de qualquer leitura/parsing do corpo.
    request.max_content_length = _MAX_BODY_BYTES

    # Rejeição antecipada por `Content-Length` continua útil como
    # otimização (evita iniciar a leitura de um corpo já anunciado como
    # grande demais), mas NUNCA é a única defesa - ver
    # `request.max_content_length` acima e o `except
    # RequestEntityTooLarge` abaixo, que cobre exatamente o caso em que
    # este header está ausente ou mentiroso.
    if request.content_length is not None and request.content_length > _MAX_BODY_BYTES:
        return _no_store_response(_INVALID_REQUEST_RESPONSE, 400)

    # `request.is_json` (Werkzeug) aceita `application/json` e variantes
    # com charset - rejeita qualquer outro Content-Type, inclusive
    # ausente, sem tentar interpretar o corpo (não lê o stream).
    if not request.is_json:
        return _no_store_response(_INVALID_REQUEST_RESPONSE, 400)

    try:
        # `silent=True`: corpo ausente ou JSON malformado vira `None`
        # (nunca a página de erro HTML padrão do Werkzeug) - tratado
        # uniformemente como requisição inválida, junto com JSON válido
        # que não seja um objeto (mesmo padrão de `app/blueprints/api.py`,
        # `/leads`). `silent=True` só suprime `ValueError` do parsing
        # JSON em si (`json_module.loads`) - a leitura do corpo
        # (`get_data()`, chamada antes do parsing) continua podendo
        # levantar `RequestEntityTooLarge` se o teto de
        # `max_content_length` for ultrapassado durante a leitura real
        # do stream, por isso o `except` explícito abaixo.
        data = request.get_json(silent=True)
    except RequestEntityTooLarge:
        return _no_store_response(_INVALID_REQUEST_RESPONSE, 400)

    if not isinstance(data, dict):
        return _no_store_response(_INVALID_REQUEST_RESPONSE, 400)

    presented_code = data.get('code')
    if (
        not isinstance(presented_code, str)
        or presented_code == ''
        or len(presented_code) > _MAX_PRESENTED_CODE_LENGTH
    ):
        return _no_store_response(_INVALID_REQUEST_RESPONSE, 400)

    # `request.authorization` (Werkzeug) nunca lança exceção para header
    # ausente, base64 malformado, ou esquema diferente de Basic - sempre
    # `None` ou um objeto com `.type != 'basic'` e `.username`/`.password`
    # como `None` (confirmado empiricamente na especificação desta
    # Issue). `WWW-Authenticate` é deliberadamente omitido da resposta
    # (mesmo padrão de `require_api_key` em `/leads`).
    auth = request.authorization
    if auth is None or auth.type != 'basic' or not auth.username or not auth.password:
        return _no_store_response(_INVALID_CREDENTIALS_OR_CODE_RESPONSE, 401)

    try:
        authorization = ProductLaunchCodeService.consume_launch_code(
            auth.username, auth.password, presented_code,
        )
    except ProductLaunchCodeError:
        # Cobre os Caminhos A, B e C (credencial inválida; código
        # inexistente/expirado/consumido/de outra instalação;
        # autorização revogada após o consumo) - sempre a MESMA resposta
        # genérica, nunca revelando qual causa ocorreu.
        return _no_store_response(_INVALID_CREDENTIALS_OR_CODE_RESPONSE, 401)
    except ProductLaunchCodeOperationError:
        current_app.logger.exception('Falha inesperada ao consumir código de lançamento')
        return _no_store_response(_INTERNAL_ERROR_RESPONSE, 500)
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            'Falha inesperada e não classificada ao consumir código de lançamento'
        )
        return _no_store_response(_INTERNAL_ERROR_RESPONSE, 500)

    return _no_store_response(dataclasses.asdict(authorization), 200)
