# HUB LiciLink

## Visão geral

O HUB é a aplicação central de identidade e administração da LiciLink: login,
gestão de organizações, membros, produtos e (em construção) a autenticação
federada que permitirá que uma organização acesse produtos parceiros (GEDO,
Kalender, Hunt) a partir de uma sessão já autenticada no HUB.

O HUB **pode ser executado e testado isoladamente**, sem nenhum dos produtos
parceiros disponível ou configurado. Login, administração, organizações,
membros, produtos e instalações já são totalmente testáveis localmente.

**Stack principal**: Python (Flask), SQLAlchemy + Alembic (migrations),
PostgreSQL, Jinja2 (templates renderizados no servidor).

**PostgreSQL é o banco oficial deste guia de instalação local** - é o único
caminho comprovadamente compatível com as migrations do projeto hoje (ver
[Solução de problemas](#solução-de-problemas) sobre SQLite).

**Situação atual da integração federada**: os blocos de autenticação
federada (credencial por instalação, código de lançamento, consumo
servidor-a-servidor) já existem e são testáveis isoladamente, mas a rota que
efetivamente inicia o handoff visual **HUB → GEDO ainda não foi
implementada**. Este README documenta o HUB como ele é hoje, não como
produto federado finalizado.

## Pré-requisitos

- **Git**.
- **Python**: testado em CI nas versões **3.11 e 3.14** (`.github/workflows/ci.yml`).
  Para desenvolvimento, **3.11 é a opção mais conservadora** (é a versão
  declarada em `pyproject.toml`). Não há evidência de que outras versões
  funcionem - use uma das duas testadas.
- **Docker com Compose v2** (comando `docker compose`, não o binário legado
  `docker-compose`) - usado para o PostgreSQL local.
- **Acesso já autorizado ao repositório privado** (`LICILINK-TECNOLOGIA/HUB-LICILINK`)
  via a autenticação GitHub que você já tem configurada na sua máquina.

## Clonando o repositório

Via HTTPS:

```bash
git clone https://github.com/LICILINK-TECNOLOGIA/HUB-LICILINK.git
cd HUB-LICILINK
```

Se você já usa SSH com o GitHub (chave já cadastrada na sua conta), pode
clonar assim no lugar do HTTPS:

```bash
git clone git@github.com:LICILINK-TECNOLOGIA/HUB-LICILINK.git
```

Nunca compartilhe seu token/chave pessoal com outra pessoa para que ela
"herde" seu acesso - cada colaborador deve ter seu próprio acesso autorizado
ao repositório.

## Ambiente virtual

O projeto usa o diretório `venv/` (já ignorado pelo Git).

### Windows PowerShell

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### Linux/macOS

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Configuração local

Copie o arquivo de exemplo para criar seu `.env` pessoal:

**Windows PowerShell:**

```powershell
Copy-Item .env.example .env
```

**Linux/macOS:**

```bash
cp .env.example .env
```

- `.env` é **local e nunca deve ser commitado** (já está no `.gitignore`).
- Cada colaborador usa **seus próprios** valores/segredos e **seu próprio**
  banco local - nunca copie o `.env` de outra pessoa.
- O PostgreSQL local do Compose (seção seguinte) é independente por
  colaborador, mesmo que os nomes sintéticos (`hub_user`/`hub_db`) sejam
  iguais para todo mundo - cada instância roda isolada na sua própria
  máquina/container.
- **Nunca use um banco manual de outra máquina ou sessão** - cada ambiente
  local deve ter seu próprio banco, criado do zero pelas migrations.

Para gerar um valor sintético local para `SECRET_KEY` (opcional em
desenvolvimento - só é obrigatório fora de `development`/`testing`), rode
localmente e cole o resultado no seu `.env` (nunca publique esse valor em
nenhum lugar, nem em commit, nem em chat, nem neste README):

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## PostgreSQL via Docker

O arquivo `docker-compose.dev.yml` (raiz do repositório) sobe **somente o
banco** - o Flask roda no seu host, fora de container.

| Item | Valor |
| --- | --- |
| Service | `db` |
| Container | `licilink_hub_db` |
| Imagem | `postgres:17` |
| Porta local (host, somente `127.0.0.1`) | `5433` |
| Porta interna (container) | `5432` |
| Database | `hub_db` |
| Usuário local sintético | `hub_user` |
| Senha local sintética | `hub_password` |
| Volume | `postgres_data` |

Essas credenciais são **sintéticas, exclusivas de desenvolvimento local** -
já são o padrão usado pelo próprio HUB quando `DATABASE_URL` está ausente em
`development` (ver `.env.example`). **Nunca devem ser usadas em
staging/produção** - este Compose é e continua sendo exclusivamente de
desenvolvimento local, nunca um modelo de configuração segura para produção.

A porta `5433` é publicada **somente em `127.0.0.1`**
(`"127.0.0.1:5433:5432"` em `docker-compose.dev.yml`) - outros dispositivos
da sua rede local **não conseguem** acessar esse PostgreSQL, mesmo
conhecendo as credenciais sintéticas acima. O HUB rodando no seu próprio
host continua se conectando normalmente por `localhost:5433`/`127.0.0.1:5433`
- `DATABASE_URL` no `.env.example` não muda.

**Iniciar:**

```bash
docker compose -f docker-compose.dev.yml up -d
```

**Aguardar o banco ficar pronto** - o Compose atual não define
`healthcheck`, então confirme explicitamente com `pg_isready` antes de
rodar qualquer migration (nunca use uma espera fixa com `sleep` como
garantia - `pg_isready` retorna assim que o banco realmente aceita
conexões):

```bash
docker compose -f docker-compose.dev.yml exec db pg_isready -U hub_user -d hub_db
```

Repita o comando acima até ele responder `accepting connections` antes de
prosseguir para as migrations.

**Encerrar preservando os dados** (uso rotineiro, ao final do dia):

```bash
docker compose -f docker-compose.dev.yml down
```

**Reset destrutivo** - **nunca use isso como parte do encerramento
cotidiano**, apenas quando você conscientemente quiser reinicializar o
banco do zero:

```bash
docker compose -f docker-compose.dev.yml down -v
```

Este comando:

- **remove o volume nomeado deste projeto Compose** (`postgres_data`,
  resolvido como `<nome-do-diretório>_postgres_data`);
- **apaga permanentemente todo o banco local** - todas as tabelas, todos os
  dados criados desde a última vez que o volume foi criado;
- **pode apagar dados acumulados de uma instalação anterior** já existente
  no mesmo clone/diretório de projeto, não só dados criados nesta sessão -
  se você (ou outra pessoa) já rodou este Compose antes nesta mesma pasta,
  `down -v` apaga esse histórico também;
- **não é reversível** - não existe backup automático desse volume.

O comando normal recomendado para o encerramento cotidiano continua sendo o
`down` (sem `-v`) mostrado acima, que preserva todos os dados.

## Banco e bootstrap

Com o PostgreSQL já respondendo (passo anterior), execute nesta ordem exata:

```bash
flask db upgrade
flask bootstrap-structural-data
flask create-admin --name "Seu Nome" --email voce@exemplo.test
```

- **`FLASK_APP` não é necessário neste projeto** - o pacote `app/` é
  descoberto automaticamente pelo Flask (não defina essa variável, é um
  passo a mais sem necessidade real aqui).
- `flask db upgrade` aplica as migrations - **precisa rodar contra
  PostgreSQL** (ver [Solução de problemas](#solução-de-problemas) sobre
  por que SQLite não funciona aqui).
- `flask bootstrap-structural-data` cria/garante o catálogo estrutural
  mínimo (papéis `owner`/`member`; produtos `kalender`/`gedo`/`hunt`) -
  é idempotente, pode ser executado mais de uma vez sem duplicar nada
  (detalhes em [`docs/structural-bootstrap.md`](docs/structural-bootstrap.md)).
- `flask create-admin` solicita a senha **de forma interativa, com entrada
  oculta e confirmação** - nunca por argumento de linha de comando. **Não
  existe senha padrão.** Cada colaborador cria seu próprio administrador
  local, com sua própria senha, escolhida na hora.
- **Execute `flask create-admin` em um terminal interativo real** (PowerShell,
  cmd ou terminal POSIX aberto normalmente) - o prompt oculto de senha lê
  diretamente do console. Ele **pode não funcionar corretamente com stdin
  redirecionado, `|`, `<`, ou qualquer automação/script não interativo**,
  especialmente no Windows (a entrada oculta pode travar em vez de ler o
  valor esperado). Isso não afeta o uso normal, manual, deste comando.
- **Nunca coloque a senha do administrador no README, no `.env`/`.env.example`,
  em um argumento de linha de comando ou em qualquer script/arquivo
  versionado ou descartável.**
- Detalhes completos e o comando para redefinir a senha de um administrador
  já existente estão em [`docs/admin-cli.md`](docs/admin-cli.md).

## Executando o HUB

Use o entrypoint oficial do projeto:

```bash
python run.py
```

Não é necessário usar `flask run` - `run.py` já é o servidor de
desenvolvimento local oficial deste projeto, com host/porta já configurados.

Por padrão, acesse:

- <http://127.0.0.1:8000/health>
- <http://127.0.0.1:8000/login>

O servidor de desenvolvimento **deve permanecer em loopback
(`127.0.0.1`)** e **nunca deve ser exposto à internet** ou à rede local sem
necessidade consciente e explícita (variável `HUB_DEV_HOST`, ver
`.env.example`).

## Testes

```bash
python -m pytest
```

Não são necessárias flags adicionais - a configuração já está em
`pyproject.toml` (`[tool.pytest.ini_options]`). A suíte usa exclusivamente
SQLite em memória para os testes automatizados (isolado do PostgreSQL local
que você configurou acima) - não é necessário ter o Compose rodando para
executar `pytest`.

## Encerramento

1. Interrompa `python run.py` (`Ctrl+C`). Se precisar confirmar que a porta
   8000 foi liberada, verifique com a ferramenta do seu sistema operacional
   (ex.: `netstat`/`Get-NetTCPConnection` no Windows, `lsof`/`ss` em
   Linux/macOS).
2. Encerre o PostgreSQL **preservando o volume** (uso rotineiro):
   ```bash
   docker compose -f docker-compose.dev.yml down
   ```
3. Desative o ambiente virtual:
   - PowerShell: `deactivate`
   - POSIX: `deactivate`
4. **Não use `down -v`** neste passo rotineiro - isso apagaria seu banco
   local por completo. Só use `down -v` quando quiser resetar
   conscientemente o ambiente.

## Solução de problemas

**Porta 5433 já em uso** - outro processo/serviço já está usando essa porta
localmente. Pare o processo conflitante ou publique o serviço `db` em outra
porta local editando `docker-compose.dev.yml` (e ajustando `DATABASE_URL`
correspondentemente no seu `.env`).

**Container PostgreSQL ainda não está pronto** - `flask db upgrade` falha
com erro de conexão se rodar antes do banco aceitar conexões. Confirme com
`docker compose -f docker-compose.dev.yml exec db pg_isready -U hub_user -d hub_db`
antes de tentar de novo.

**`DATABASE_URL` ausente ou incorreta** - em desenvolvimento, se ausente, o
HUB usa o padrão que já corresponde ao Compose acima; se você definiu um
valor no `.env` e ele não bate com o Compose (usuário/senha/porta/database
diferentes), corrija o valor ou pare de sobrescrever essa variável.

**Migration não executada** - se o HUB reclamar de tabela/coluna
inexistente, confirme que `flask db upgrade` foi executado com sucesso
contra o PostgreSQL correto (`flask db current` mostra a revisão atual).

**Ambiente virtual não ativado** - se `flask`/`python -m pytest` não forem
encontrados ou usarem uma instalação de Python diferente da esperada,
confirme que o ambiente virtual está ativo (o prompt do terminal deve
mostrar `(venv)`).

**PowerShell não encontra o Python correto** - confirme com
`Get-Command python` qual `python.exe` está sendo usado; se necessário, crie
o ambiente virtual explicitamente com o interpretador desejado (ex.:
`py -3.11 -m venv venv`).

**Tentativa de usar SQLite** - **SQLite não é um caminho oficial de
instalação local neste projeto.** O schema atual usa tipos específicos de
PostgreSQL (incluindo `JSONB`), e tanto `flask db upgrade` quanto
`db.create_all()` falham contra SQLite sem um adaptador de compatibilidade
que hoje só existe dentro de `tests/conftest.py` - exclusivo da suíte
automatizada, nunca carregado pela aplicação em execução normal. **Não
copie esse adaptador nem crie um wrapper próprio** para "fazer SQLite
funcionar" localmente - use sempre o PostgreSQL deste guia. (A correção da
Issue #69 resolveu um problema de carregamento de UUID ao ler um usuário de
um banco SQLite *já existente* - isso é diferente de criar o schema do
zero, que continua exigindo PostgreSQL.)

## Limitações atuais

- O HUB pode ser testado isoladamente - **o GEDO não é necessário** para
  testar login, administração, organizações, produtos ou instalações.
- URLs como as configuradas em `L_GEDO_URL`/`L_KALENDER_URL`/`L_HUNT_URL`
  (ex.: `https://gedo.local`) são **exemplos sintéticos** - não precisam
  resolver de verdade para testar cadastro, instalação ou emissão de código
  localmente.
- **A rota `POST` do launcher (o handoff federado em si) ainda não foi
  implementada.**
- **O fluxo visual completo HUB → GEDO permanece pendente.**
- Planos e quotas comerciais **não fazem parte** deste setup local.
- Deploy, SSH de servidor e configuração de produção **estão fora do escopo
  deste README** - este guia cobre exclusivamente onboarding de
  desenvolvimento local.

## Segurança

- Nunca compartilhe seu `.env` com outra pessoa.
- Nunca versione segredos (o `.gitignore` já protege `.env` e bancos `.db`
  locais - não burle essa proteção).
- Nunca use um banco real (de staging/produção) para desenvolvimento local.
- Nunca use credenciais reais (de cliente, de produção ou de outro
  colaborador) neste ambiente.
- Nunca exponha o modo debug do Flask (`python run.py`) para fora de
  `127.0.0.1`/sua rede de confiança.
- Nunca reutilize, neste ambiente local, uma credencial (`SECRET_KEY`,
  senha, API key) que também seja usada em produção.
- Nunca envie chave privada (SSH, `.pem`, etc.) para o repositório.
