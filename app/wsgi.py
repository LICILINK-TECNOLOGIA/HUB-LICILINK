"""Entry point WSGI do HUB para staging e produção.

Uso: `gunicorn app.wsgi:application`. Importar este módulo cria a aplicação
e falha, sem cair em 'development', se `FLASK_ENV` não for `staging` ou
`production` ou se alguma exigência de implantação não estiver atendida
(ver `app/deployment.py`).

Fica dentro do pacote `app/` de propósito: um `wsgi.py` na raiz seria
importado automaticamente pelo comando `flask` e quebraria o
desenvolvimento local, que roda sem `FLASK_ENV`.
"""
from .deployment import create_deployed_app

application = create_deployed_app()
