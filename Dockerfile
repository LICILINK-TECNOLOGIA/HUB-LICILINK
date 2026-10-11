# Imagem do HUB para staging/produção (Gunicorn). Roteiro: docs/staging-deploy.md.
#
# Nenhum segredo entra aqui: não há ARG nem ENV com credencial, e o .dockerignore
# mantém .env*, instance/, venv/ e .git/ fora do contexto. Segredos chegam só em
# tempo de execução, pelo ambiente do contêiner.

# Python 3.14: uma das duas versões testadas pelo CI (.github/workflows/ci.yml).
FROM python:3.14-slim

# FLASK_APP faz o comando `flask` desta imagem (por exemplo `flask db upgrade`)
# passar pelo mesmo entry point do servidor: sem FLASK_ENV=staging ou production
# ele falha, em vez de cair em 'development'.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FLASK_APP=app.wsgi:application

RUN groupadd --system --gid 10001 hub \
    && useradd --system --uid 10001 --gid hub --no-create-home --shell /usr/sbin/nologin hub

WORKDIR /srv/hub

COPY requirements.txt ./
RUN python -m pip install -r requirements.txt

# Só o código da aplicação e as migrations. Testes, docs, arquivos de compose e
# qualquer arquivo local ficam fora da imagem.
COPY app ./app
COPY migrations ./migrations

USER 10001:10001

EXPOSE 8000

# Usa a rota /health que já existe e o próprio Python da imagem (nenhuma
# ferramenta instalada só para isso). Prova que o processo responde HTTP; a
# rota não consulta o banco.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"]

# Um processo, várias threads: o limitador de login guarda os contadores na
# memória do processo, e com mais de um processo cada um teria os seus. Vale
# só para a homologação; não é solução de produção (docs/staging-deploy.md).
# --no-control-socket: a interface de controle do Gunicorn não é usada e, por
# padrão, criaria um socket na pasta pessoal do usuário, que não existe aqui.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--worker-class", "gthread", "--worker-tmp-dir", "/dev/shm", "--no-control-socket", "app.wsgi:application"]
