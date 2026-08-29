import os

from app import create_app

# Servidor de desenvolvimento local exclusivamente. Não é um entry point WSGI:
# nada aqui roda ao importar este módulo, apenas ao executar `python run.py`.
DEFAULT_DEV_HOST = '127.0.0.1'
DEFAULT_DEV_PORT = 8000


def _resolve_dev_port(raw_port):
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        raise RuntimeError(
            "HUB_DEV_PORT inválida: deve ser um número inteiro entre 1 e 65535."
        )

    if not (1 <= port <= 65535):
        raise RuntimeError(
            "HUB_DEV_PORT inválida: deve estar entre 1 e 65535."
        )

    return port


def main():
    host = os.getenv('HUB_DEV_HOST', DEFAULT_DEV_HOST)
    port = _resolve_dev_port(os.getenv('HUB_DEV_PORT', DEFAULT_DEV_PORT))

    app = create_app('development')
    app.run(debug=app.debug, host=host, port=port)


if __name__ == '__main__':
    main()
