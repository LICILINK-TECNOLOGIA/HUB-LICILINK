import logging

from flask import render_template, request
from flask_wtf.csrf import CSRFError

logger = logging.getLogger(__name__)


def register_error_handlers(app):
    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        # Log sanitizado: somente método e endpoint da requisição rejeitada.
        # Nunca o motivo interno do Flask-WTF (`error.description`), token,
        # cookie, corpo ou parâmetros - nada que possa vazar segredo ou
        # detalhe interno de validação.
        logger.warning(
            "Falha de validação CSRF: method=%s endpoint=%s",
            request.method,
            request.endpoint,
        )
        return render_template("errors/csrf.html"), 400
