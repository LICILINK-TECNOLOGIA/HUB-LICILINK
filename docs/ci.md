# Integração contínua (CI)

O workflow `.github/workflows/ci.yml` valida automaticamente todo Pull
Request direcionado a `main` e todo push direto para `main`.

## Quando roda

- Em qualquer Pull Request aberto ou atualizado com destino `main`.
- Em qualquer push direto para `main` (por exemplo, após um merge).

Cada nova execução na mesma branch/PR cancela automaticamente a execução
anterior ainda em andamento (`concurrency` + `cancel-in-progress`).

## Matriz de versões do Python

O job `test` roda em paralelo para:

- **Python 3.11**
- **Python 3.14**

`3.14` é a versão usada e validada localmente durante todo o
desenvolvimento do projeto até aqui (é o que efetivamente já rodou os 267
testes da suíte em todas as issues anteriores). `3.11` foi incluída na
matriz porque é a versão indicada em `[tool.ruff] target-version` do
`pyproject.toml`, mas **ainda não havia sido validada em nenhum momento
neste projeto antes desta Issue** - a matriz existe justamente para
produzir essa primeira validação real. Não afirme "3.11 é suportado"
antes de ver o check correspondente passar pela primeira vez no GitHub;
até lá, trate como "em validação".

Os nomes dos checks gerados pela matriz (`test (Python 3.11)` e
`test (Python 3.14)`) devem permanecer estáveis - uma futura configuração
de branch protection (fora do escopo desta Issue) dependeria desses nomes
exatos para exigi-los antes de permitir merge.

## O que o workflow executa

Para cada versão da matriz, nesta ordem:

1. `python -m pip install -r requirements.txt`
2. `pytest`
3. `python -m compileall -q app tests run.py migrations`

Nenhum outro passo é executado - sem lint, formatter, type checker ou
cobertura (nenhum desses está configurado como obrigatório no projeto
hoje).

## Isolamento e ausência de segredos

- Os testes usam **exclusivamente SQLite em memória**
  (`TestingConfig.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'`) - nenhum
  banco real é criado, iniciado ou acessado pelo CI.
- `create_app('testing')` não depende de nenhuma variável de ambiente
  (`SECRET_KEY`, `DATABASE_URL` e as demais já têm valor fixo/seguro para
  testes na própria configuração) - por isso o workflow não define nenhuma
  variável de ambiente nem usa `secrets.*` em nenhum passo.
- O job roda com permissão `contents: read` apenas, e faz checkout com
  `persist-credentials: false`: como `pytest` executa código do próprio
  Pull Request (fixtures, `conftest.py`, testes), nenhuma credencial de
  escrita/push fica disponível nesse ambiente, mesmo que o código do PR
  tente acessá-la.
- O evento usado é `pull_request` (nunca `pull_request_target`) - PRs de
  fork rodam com token restrito e sem acesso a segredos do repositório,
  pelo próprio modelo de segurança do GitHub Actions.

## Como reproduzir localmente

A partir da raiz do projeto, com o ambiente virtual do projeto ativo:

```
python -m pip install -r requirements.txt
pytest
python -m compileall -q app tests run.py migrations
```

São exatamente os mesmos três comandos que o CI executa - se passarem
localmente, é o resultado mais próximo possível do que o CI vai reportar
(ressalvada a versão de Python usada localmente, ver matriz acima).

## Como ver os logs

Na aba **"Checks"** do Pull Request (ou na aba **"Actions"** do
repositório), procure pela execução do workflow **CI** e abra o job
correspondente à versão do Python que falhou
(`test (Python 3.11)`/`test (Python 3.14)`) para ver a saída completa de
cada passo.

## Como interpretar uma falha

- **Falha em "Instalar dependências"**: problema ao resolver/instalar um
  pacote de `requirements.txt` para aquela versão específica do Python
  (ex.: uma dependência sem wheel disponível para essa versão) - não é um
  problema de código do projeto.
- **Falha em "Executar suíte de testes"**: um ou mais testes quebraram -
  role a saída do `pytest` até o resumo no final para ver quais testes
  falharam e por quê.
- **Falha em "Verificar sintaxe"**: `compileall` encontrou um erro de
  sintaxe em algum arquivo de `app/`, `tests/`, `run.py` ou `migrations/` -
  a mensagem indica o arquivo e a linha exatos.

## O que este CI explicitamente NÃO faz

- Não configura branch protection - isso é uma decisão e uma ação
  separadas, fora do escopo da Issue #25.
- Não valida migrations contra um PostgreSQL real, nem executa
  `flask bootstrap-structural-data` ou `flask create-admin` - essa
  validação de instalação limpa com banco real é o objeto da Issue #26,
  feita manualmente em ambiente descartável, não neste workflow automático.
- Não faz deploy, não faz push, não comenta em Pull Requests, não altera
  Issues e não executa nenhum comando administrativo.
