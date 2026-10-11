# Implantação do HUB em homologação (Docker, Gunicorn e Traefik)

> **Este documento prepara uma implantação; não registra uma.** Nada aqui foi
> executado no VPS. A imagem foi construída e validada só localmente, com dados
> sintéticos. A implantação em homologação depende dos portões da seção 7 e das
> pendências da seção 11.

O desenvolvimento local não muda: continua com `python run.py`,
`docker-compose.dev.yml` e o `README.md`.

## 1. O que o repositório entrega

| Arquivo | Finalidade |
|---|---|
| `Dockerfile` | Imagem de execução: Python 3.14, Gunicorn, usuário não root. |
| `.dockerignore` | Mantém `.env*`, `instance/`, `venv/`, `.git/`, caches e bancos locais fora da imagem. |
| `compose.staging.yml` | Configuração comum: serviço `hub` atrás do Traefik existente e o comando avulso `migrate`. Liga o TLS no router sem escolher a origem do certificado. |
| `compose.staging.acme.yml` | Override do caminho ACME: acrescenta só a label do resolvedor de certificados. |
| `app/wsgi.py` | Entry point do Gunicorn (`app.wsgi:application`). |
| `app/deployment.py` | Exigências de implantação e `ProxyFix`. |
| `deploy/staging/app.env.example` | Nomes e descrições das variáveis do contêiner. |
| `deploy/staging/compose.env.example` | Nomes e descrições das variáveis do Compose. |

## 2. Premissas do VPS

Estes fatos **não foram conferidos**. São premissas informadas, e cada uma precisa
ser confirmada no VPS antes da implantação:

- cadeia de acesso prevista: navegador → Cloudflare → Traefik → HUB;
- o Traefik já existe e usa a rede Docker externa `licilink-hml`;
- domínio de homologação: `hub-hml.licilink.com.br`;
- PostgreSQL de homologação no servidor `licilink-postgres-hml`.

Não são conhecidos, e por isso **não têm valor no repositório**:

- o nome do entrypoint HTTPS do Traefik (`TRAEFIK_ENTRYPOINT`);
- de onde vem o certificado TLS do domínio: ACME ou certificado fixo (seção 5);
- no caminho ACME, o nome do resolvedor de certificados
  (`TRAEFIK_CERTRESOLVER`);
- a quantidade de proxies confiáveis (`HUB_PROXY_FIX_X_FOR` e
  `HUB_PROXY_FIX_X_PROTO`);
- se o contêiner do HUB alcança o PostgreSQL pela rede `licilink-hml` ou se
  precisa de outra rede.

## 3. Como a aplicação inicia

- O Gunicorn carrega `app.wsgi:application`. Esse módulo exige `FLASK_ENV`
  presente e igual a `staging` ou `production`. Ausente ou com outro valor, a
  aplicação **não inicia**; ela nunca cai em `development`. O
  `compose.staging.yml` define `FLASK_ENV: staging` explicitamente.
- Dentro da imagem, o comando `flask` usa o mesmo entry point (`FLASK_APP` já
  vem definida na imagem). `flask db upgrade` sem `FLASK_ENV` também falha.
- A imagem nunca usa `flask run` nem `python run.py`.
- As mesmas exigências valem para `production`. Este documento prepara só a
  homologação.

Variáveis obrigatórias ausentes ou inválidas impedem a inicialização. As
mensagens citam só o nome da variável e a regra, nunca o valor recebido.

### Um processo, várias threads

O Gunicorn roda com **um processo e quatro threads**. O motivo é o limitador de
login: ele guarda os contadores na memória do processo. Com mais de um processo,
cada um teria os próprios contadores e o limite efetivo seria multiplicado.

**Isso não é solução de produção.** Reiniciar o contêiner zera os contadores, e
mais de um processo ou mais de uma réplica exigem armazenamento compartilhado
para o limitador, ou outra decisão. Não aumente `--workers` nem suba réplicas do
serviço enquanto isso não for resolvido.

Na inicialização, o Flask-Limiter avisa que está usando armazenamento em
memória. O aviso é esperado nesta configuração.

### Healthcheck

O healthcheck da imagem faz `GET /health` com o próprio Python da imagem. Ele
prova que **o processo responde HTTP**. A rota `/health` não consulta o banco:
um contêiner `healthy` não prova que o PostgreSQL está acessível. A conexão com
o banco é conferida pelas migrations (passo 3 da seção 8) e pelos testes
funcionais (passo 7).

### Processo e sistema de arquivos

- O processo roda como usuário não root (`uid` 10001).
- O compose monta a raiz do contêiner como somente leitura, com `/tmp` em
  memória, sem capacidades do kernel e com `no-new-privileges`.
- Só a porta interna 8000 é exposta, na rede Docker. **Nenhuma porta é publicada
  no host**; não acrescente `ports` ao compose.

## 4. Variáveis

Nomes e descrições completos:
[`deploy/staging/compose.env.example`](../deploy/staging/compose.env.example) e
[`deploy/staging/app.env.example`](../deploy/staging/app.env.example).

**Do Compose, sempre obrigatórias** (sem valor padrão, nenhuma é segredo):
`COMPOSE_PATH_SEPARATOR`, `COMPOSE_FILE`, `HUB_IMAGE_TAG`, `HUB_ENV_FILE`,
`TRAEFIK_ENTRYPOINT`.

**Do Compose, só no caminho ACME:** `TRAEFIK_CERTRESOLVER`.

**Do contêiner, obrigatórias:** `SECRET_KEY`, `DATABASE_URL`,
`HUB_PROXY_FIX_X_FOR`, `HUB_PROXY_FIX_X_PROTO`, `EMAIL_PROVIDER`,
`L_KALENDER_URL`, `L_GEDO_URL`, `L_HUNT_URL`.

**Do contêiner, obrigatórias com `EMAIL_PROVIDER=resend`:** `RESEND_API_KEY`,
`EMAIL_FROM`.

**Do contêiner, opcionais:** `EMAIL_REPLY_TO`, `HUB_API_KEY`,
`VERIFICATION_CODE_TTL`, `VERIFICATION_MAX_ATTEMPTS`,
`VERIFICATION_RESEND_COOLDOWN`, `VERIFICATION_MAX_RESENDS`,
`INSTALLATION_CREDENTIAL_ROTATION_GRACE_SECONDS`,
`PRODUCT_LAUNCH_CODE_TTL_SECONDS`.

### Segredos

- Nenhum segredo fica no repositório nem na imagem. A imagem não recebe segredo
  por `ARG` nem por `ENV`.
- Os segredos chegam só em tempo de execução, pelo arquivo indicado em
  `HUB_ENV_FILE`. Esse arquivo fica **fora do repositório**, com permissão
  restrita ao usuário que opera o Docker.
- No arquivo, use aspas simples em valores que contenham cifrão, para o Compose
  não tentar interpolá-los.
- `SECRET_KEY`, `RESEND_API_KEY` e `HUB_API_KEY` são exclusivos da homologação,
  nunca reaproveitados de outro ambiente.
- `docker compose config`, sem filtro, imprime as variáveis do contêiner já
  resolvidas, inclusive os segredos. Use-o sempre com filtro, como nos comandos
  deste roteiro.

### URLs dos produtos

`L_KALENDER_URL`, `L_GEDO_URL` e `L_HUNT_URL` não têm valor padrão em
homologação: precisam ser declaradas, em `https`, com host e sem credenciais.
Elas são as URLs canônicas usadas pelo catálogo e pela validação das instalações.

No launcher, um produto sem assinatura ativa, sem instalação ou com instalação
inativa aparece com o botão desabilitado ("Em breve", "Configuração pendente" ou
"Aguardando ativação"). Só uma instalação ativa gera o botão "Acessar Sistema".

## 5. Certificado TLS: dois caminhos

O router do HUB sempre tem TLS ligado. **De onde vem o certificado não é
escolhido de forma implícita:** há dois caminhos, cada um com o seu conjunto de
arquivos de compose. Qual deles o Traefik do VPS usa ainda não é conhecido, e
nenhum dos dois foi exercitado contra um Traefik real.

Nenhum certificado, chave privada ou caminho real do VPS entra no repositório.

### ACME

Usar `compose.staging.yml` **junto com** `compose.staging.acme.yml`. O segundo
arquivo acrescenta só a label `tls.certresolver`, com o valor de
`TRAEFIK_CERTRESOLVER`, que passa a ser obrigatória.

Antes de implantar, confirmar no VPS:

- o nome real do resolvedor de certificados;
- o tipo de desafio que ele usa;
- que o desafio funciona com o domínio passando pela Cloudflare.

### Certificado fixo

Usar **somente** `compose.staging.yml`. Nesse caminho o Traefik precisa já ter o
certificado do domínio, e `TRAEFIK_CERTRESOLVER` não é usada.

Antes de implantar, confirmar:

- que o certificado correspondente ao domínio está carregado na configuração do
  Traefik;
- a validade do certificado e como ele é renovado;
- que a Cloudflare está em Full (strict);
- que o router não tem a label `tls.certresolver` (conferência do passo 2 da
  seção 8).

## 6. Um único conjunto de arquivos por implantação

Com dois arquivos de compose, build, migrations, start, `ps` e `logs` precisam
usar **exatamente o mesmo conjunto**. Um comando com conjunto diferente pode
subir o serviço sem a label do resolvedor, sem erro algum.

Por isso o conjunto é escolhido **uma única vez**, no arquivo de variáveis do
Compose (fora do repositório, formato em
[`deploy/staging/compose.env.example`](../deploy/staging/compose.env.example)):

| Caminho TLS | `COMPOSE_FILE` |
|---|---|
| Certificado fixo | `compose.staging.yml` |
| ACME | `compose.staging.yml:compose.staging.acme.yml` |

O mesmo arquivo declara `COMPOSE_PATH_SEPARATOR=:`, para o separador da lista
não depender do sistema operacional.

Todo comando deste roteiro tem a mesma forma, rodado **na raiz do repositório**:

```
docker compose --env-file "$HUB_COMPOSE_ENV" <comando>
```

`HUB_COMPOSE_ENV` é só o caminho desse arquivo, guardado numa variável do shell
no início da sessão. **Nunca use `-f`.**

### O que foi confirmado sobre esse comportamento

Conferido no Docker Compose v5.4.0, a versão usada na validação local:

- `--env-file` aplica a `COMPOSE_FILE` e a `COMPOSE_PATH_SEPARATOR` declaradas
  no arquivo;
- sem `COMPOSE_FILE` e sem `-f`, o comando **falha** ("no configuration file
  provided"), porque o repositório não tem arquivo de compose com nome padrão.
  Esquecer o `--env-file` gera erro, não um conjunto diferente;
- `-f` **ignora** a `COMPOSE_FILE`. É a forma de subir o serviço sem a label do
  resolvedor sem perceber, e por isso não aparece neste roteiro;
- uma `COMPOSE_FILE` exportada no shell **prevalece** sobre a do arquivo. O
  shell da implantação não pode ter essa variável definida;
- os caminhos da `COMPOSE_FILE` são relativos à pasta atual. Fora da raiz do
  repositório o comando falha por não achar o arquivo;
- sem `COMPOSE_PATH_SEPARATOR`, a lista separada por dois-pontos não funcionou
  no Windows. O comportamento padrão no Linux não foi exercitado, e a variável
  declarada torna isso irrelevante.

A versão do Compose no VPS pode ser outra. A conferência do passo 2 da seção 8
repete a verificação lá.

## 7. Portões: quando a implantação deve parar

A implantação no VPS **não prossegue** enquanto qualquer um destes portões não
estiver cumprido:

1. **Proxies confiáveis.** A configuração real de `forwardedHeaders` e de
   `trustedIPs` do Traefik foi verificada, a quantidade de proxies confiáveis foi
   determinada a partir dela, e `HUB_PROXY_FIX_X_FOR` e `HUB_PROXY_FIX_X_PROTO`
   estão configuradas com esse valor, **diferente de 0**.
2. **E-mail.** `EMAIL_PROVIDER` é diferente de `console`, com o provedor aprovado
   para a homologação (hoje, `resend`), credencial própria de homologação e
   controle de destinatários.
3. **Proteção de acesso.** A proteção de acesso da homologação foi decidida e
   aplicada (seção 11).

O valor 0 nas variáveis de proxy e o provedor `console` existem só para construir
e validar a imagem localmente. Nos dois casos a aplicação registra um aviso em
nível WARNING ao iniciar. Eles **não autorizam** a implantação externa.

## 8. Ordem da implantação

Sempre nesta ordem, na raiz do repositório, com o mesmo arquivo de variáveis do
Compose em todos os comandos (seção 6):

```
HUB_COMPOSE_ENV=/caminho/fora/do/repositorio/arquivo-de-variaveis-do-compose
```

1. **Build.**

   ```
   docker compose --env-file "$HUB_COMPOSE_ENV" build hub
   ```

2. **Conferência e backup.** Conferir os três portões da seção 7 e parar se
   algum não estiver cumprido. Conferir também:

   - **o caminho TLS em uso.** O shell não pode ter `COMPOSE_FILE` exportada, e o
     conjunto efetivo precisa ser o do caminho decidido na seção 5:

     ```
     env | grep '^COMPOSE_'
     docker compose --env-file "$HUB_COMPOSE_ENV" config --environment | grep '^COMPOSE_FILE='
     docker compose --env-file "$HUB_COMPOSE_ENV" config | grep -c 'tls.certresolver'
     ```

     O primeiro comando não deve mostrar nada. O último deve mostrar **1** no
     caminho ACME e **0** no caminho de certificado fixo. Qualquer outro
     resultado: parar;
   - as confirmações do caminho TLS escolhido (seção 5);
   - as premissas da seção 2, com o nome real do entrypoint preenchido;
   - que o banco e o usuário do HUB existem e são exclusivos (seção 10);
   - que o contêiner alcança o PostgreSQL pela rede prevista.

   Fazer o backup do banco do HUB antes de qualquer migration e guardar o
   resultado fora do contêiner. Na primeira implantação o banco está vazio; o
   backup passa a valer das seguintes em diante.

3. **Migrations, uma única vez por implantação.**

   ```
   docker compose --env-file "$HUB_COMPOSE_ENV" --profile ops run --rm migrate
   ```

   As migrations **nunca** rodam na inicialização do contêiner. O serviço
   `migrate` só existe no perfil `ops`, não sobe com `up`, não tem labels do
   Traefik e termina ao concluir. Uma segunda execução não aplica nada.

   Só na primeira implantação, depois das migrations, os dois comandos de carga
   inicial já descritos no `README.md`:

   ```
   docker compose --env-file "$HUB_COMPOSE_ENV" --profile ops run --rm migrate flask bootstrap-structural-data
   docker compose --env-file "$HUB_COMPOSE_ENV" --profile ops run --rm migrate flask create-admin --name "..." --email ...
   ```

   O `create-admin` pede a senha de forma interativa.

4. **Start.**

   ```
   docker compose --env-file "$HUB_COMPOSE_ENV" up -d hub
   ```

5. **Health.** Esperar o contêiner ficar `healthy` e ler os avisos da
   inicialização. Um aviso sobre `HUB_PROXY_FIX_X_FOR=0`,
   `HUB_PROXY_FIX_X_PROTO=0` ou `EMAIL_PROVIDER=console` significa que um portão
   foi ignorado: parar.

   ```
   docker compose --env-file "$HUB_COMPOSE_ENV" ps
   docker compose --env-file "$HUB_COMPOSE_ENV" logs hub
   ```

6. **HTTPS.** Conferir, de fora, que `https://hub-hml.licilink.com.br/health`
   responde com certificado válido para o domínio, vindo do caminho TLS
   escolhido, e que a proteção de acesso se comporta como decidido.

7. **Testes funcionais.** Login, cadastro com e-mail real do provedor aprovado,
   launcher e, se aplicável, o lançamento federado. Conferir na auditoria que o
   endereço gravado é o do cliente, não o de um proxy.

## 9. Proxy reverso, Cloudflare e limitador

O limitador de login identifica o cliente por `request.remote_addr`, e a
auditoria grava o mesmo endereço. Atrás de proxies, esse endereço é o do último
proxy, a menos que a aplicação seja instruída a confiar nos cabeçalhos
encaminhados.

Essa instrução é explícita e separada por cabeçalho:

- `HUB_PROXY_FIX_X_FOR`: quantos valores de `X-Forwarded-For` são confiáveis;
- `HUB_PROXY_FIX_X_PROTO`: quantos valores de `X-Forwarded-Proto` são confiáveis.

Os valores são contados a partir do proxy mais próximo do HUB. `X-Forwarded-Host`,
`X-Forwarded-Port` e `X-Forwarded-Prefix` nunca são lidos.

**Não existe valor padrão em homologação, e o número não pode ser escolhido
contando contêineres.** A cadeia navegador → Cloudflare → Traefik → HUB não
equivale automaticamente a 1. O valor depende de:

- como o Traefik trata o `X-Forwarded-For` que recebe (`forwardedHeaders`,
  `trustedIPs`): se preserva e acrescenta, ou se descarta o que veio de fora;
- se os endereços da Cloudflare estão entre os confiáveis do Traefik;
- se o endereço do cliente chega por `X-Forwarded-For` ou só por
  `CF-Connecting-IP`, que o HUB não lê.

Um número maior que o real faz o HUB aceitar um endereço escrito pelo próprio
cliente. Um número menor faz o HUB tratar um proxy como se fosse o cliente.

### Quando o HUB pode confiar nos cabeçalhos

Só quando as duas condições valem:

1. o HUB recebe tráfego **exclusivamente** do Traefik. Qualquer outro caminho até
   a porta 8000 permite forjar os cabeçalhos;
2. o Traefik **substitui ou valida** os cabeçalhos vindos da internet, em vez de
   repassar o que o cliente enviou.

### Com 0 (desligado)

- Todos os usuários dividem o mesmo limite de login: cinco tentativas por minuto
  para a homologação inteira.
- A auditoria grava o endereço do proxy, não o do cliente.
- Com `HUB_PROXY_FIX_X_PROTO=0`, o HUB enxerga as requisições como HTTP mesmo
  quando o usuário está em HTTPS.

### Com o protocolo encaminhado

Quando `HUB_PROXY_FIX_X_PROTO` está correto, o HUB reconhece a requisição como
HTTPS. Nesse caso a proteção CSRF também confere o cabeçalho `Referer`, que o
navegador envia nos formulários da mesma origem. Uma política que remova o
`Referer` dessas páginas faria os formulários falharem.

## 10. Banco de dados

O HUB usa **um banco e um usuário PostgreSQL exclusivos**, mesmo dividindo o
servidor `licilink-postgres-hml` com outros sistemas:

- o banco do HUB não é o banco de nenhum outro produto;
- o usuário do HUB só tem acesso ao banco do HUB, e nenhum outro sistema usa
  esse usuário;
- `DATABASE_URL` aponta para esse banco, com esse usuário.

Criar o banco e o usuário é tarefa do VPS; nada neste repositório os cria.

## 11. Pendências do VPS, decisões em aberto e tarefas futuras

**Proteção de acesso da homologação.** A homologação não deve ficar publicamente
acessível sem decisão explícita. Falta decidir a proteção (Cloudflare Access,
VPN, lista de endereços permitidos ou equivalente). O compose não declara nenhum
middleware para isso, de propósito.

Junto com ela, falta decidir **como a chamada servidor a servidor do GEDO ao HUB
continua funcionando**. O GEDO consome o código de lançamento em
`POST /api/federation/launch/consume`, sem navegador e sem sessão. Uma proteção
que exija login interativo bloquearia essa chamada.

**A conferir no VPS:**

- o caminho TLS em uso e as confirmações dele (seção 5);
- configuração de `forwardedHeaders` e `trustedIPs` do Traefik;
- tratamento de `X-Forwarded-For` e de `CF-Connecting-IP` na cadeia real;
- quantidade correta de proxies confiáveis;
- garantia de que a porta interna do HUB não é acessível de fora, nem por outro
  contêiner que não seja o Traefik;
- nome real do entrypoint HTTPS;
- versão do Docker Compose e o comportamento da seleção de arquivos (seção 6);
- caminho de rede do HUB até o PostgreSQL;
- existência do banco e do usuário exclusivos;
- procedimento de backup do banco do HUB.

**Depende de decisão:** a proteção de acesso, o controle de destinatários do
e-mail em homologação e o armazenamento do limitador, se um dia houver mais de
um processo.

**Tarefas anteriores à produção:**

- fixar a imagem base por digest. Hoje o `Dockerfile` usa a etiqueta
  `python:3.14-slim`, que muda com o tempo;
- separar as dependências de execução das de desenvolvimento. Hoje a imagem
  instala o `requirements.txt` único do projeto, que inclui ferramentas de teste
  e de lint.

## 12. Validação local da imagem

O que foi exercitado localmente, com contêineres descartáveis e dados
sintéticos:

- construção da imagem;
- recusa de inicialização com `FLASK_ENV` ausente ou diferente de `staging` e
  `production`, e com variável obrigatória ausente ou inválida;
- inicialização com configuração sintética e healthcheck `healthy`;
- migrations aplicadas uma vez num PostgreSQL sintético, pelo comando avulso;
- limitador e protocolo encaminhado, por HTTP, com o proxy ligado e desligado;
- processo como usuário não root, raiz somente leitura e nenhuma porta
  publicada;
- os dois conjuntos de arquivos de compose, só com `docker compose config`: as
  labels de cada caminho e as variáveis obrigatórias de cada um.

**Não foi exercitado:** o Traefik, a Cloudflare, nenhum dos dois caminhos de
certificado, o domínio, o compose com a rede `licilink-hml` real, o envio de
e-mail pelo Resend e o PostgreSQL de homologação.
