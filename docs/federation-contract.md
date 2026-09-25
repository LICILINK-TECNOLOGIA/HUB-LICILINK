# Contrato federado HUB ↔ produtos (V1, lado HUB)

**Versão do contrato:** `1` (`contract_version` na resposta de sucesso;
`_CONTRACT_VERSION` em `app/services/product_launch_code_service.py`).

**Escopo deste documento:** descreve exclusivamente o que o **HUB** faz hoje.
Ele não define nem confirma nenhum comportamento do GEDO (ou de qualquer
outro produto). O lado GEDO não foi inspecionado nem homologado nesta etapa.
O receptor e o cliente servidor-a-servidor do GEDO ainda precisam ser
disponibilizados e confirmados para a integração. Tudo o que depende do GEDO
está marcado como **pendente de confirmação**.

**Referências:** arquitetura em #62; modelo de instalação #63 e #68;
credencial #64; código de lançamento #65 e #66; consumo #67; launcher #71;
homologação isolada do lado HUB #72.

Documento complementar: [`federation-credentials.md`](federation-credentials.md)
(provisionamento, emissão, rotação e revogação de credenciais).

## 1. Visão geral e responsabilidades

```
Navegador ──(1) POST /launch/<product_code>──▶ HUB
Navegador ◀─(2) página de handoff (HTML) ───── HUB
Navegador ──(3) POST application/x-www-form-urlencoded, campo "code" ──▶ produto (GEDO)
Produto   ──(4) POST /api/federation/launch/consume (JSON + Basic) ─────▶ HUB
Produto   ◀─(5) 200 + contrato JSON  |  401 genérico ─────────────────── HUB
```

| Parte | Responsabilidade |
|---|---|
| **HUB** | Revalidar a autorização no clique; emitir o código opaco; entregar o formulário de handoff; autenticar a instalação; consumir o código atomicamente; devolver o contrato de identidade/autorização (ou um `401` genérico). |
| **GEDO** (pendente de confirmação) | Receber o `POST` do navegador; chamar o endpoint de consumo do HUB por servidor-a-servidor; decidir sessão local, duração, renovação, encerramento e permissões internas. |
| **Proxy/deploy** | Terminar TLS; encaminhar corretamente o esquema seguro; não definir cookies do HUB com `Domain` que alcance os produtos. |

## 2. Etapa 1: emissão e handoff pelo navegador

### 2.1 Rota

`POST /launch/<product_code>` (`app/blueprints/dashboard.py`), com sessão do
HUB (`@login_required`) e proteção CSRF do próprio HUB. `GET` na mesma URL
responde `405`. A organização corrente é resolvida no servidor
(`OrganizationService.resolve_current_organization`); nada de organização,
instalação ou URL de destino vem do navegador. `product_code` é um código
canônico (`kalender`, `gedo` ou `hunt`).

A cada `POST` bem-sucedido, `ProductLaunchCodeService.issue_launch_code`
revalida usuário, e-mail verificado, vínculo ativo, organização ativa,
assinatura `active`/`trial` e instalação ativa com URL válida, e emite
**um** código. Uma rejeição de domínio vira mensagem `flash` e redirecionamento
(`302`) para o launcher, sem código. Uma falha operacional propaga como erro
do Flask (não vira mensagem de aparência normal).

### 2.2 Resposta de sucesso (página de handoff)

HTML (`app/templates/dashboard/launch_handoff.html`) com os headers
`Cache-Control: no-store`, `Pragma: no-cache` e `Referrer-Policy: no-referrer`
(constante `_NO_STORE_HEADERS`; verificados em `tests/test_launcher_launch_route.py`
e `tests/test_launcher_launch_handoff.py`).

O formulário de handoff:

- `method="post"` e `action` igual à `url` da instalação persistida no HUB
  (`destination_url`), nunca um valor recebido do navegador;
- sem `enctype`, portanto o corpo é `application/x-www-form-urlencoded`;
- um único campo `<input type="hidden" name="code">` com o código;
- um botão "Continuar" (sem `name`, não envia campo) e um script externo
  (`static/js/launch_handoff.js`, `defer`) que chama `form.submit()`. Se o
  script falhar, o botão permanece como alternativa manual.

O HUB não coloca o código em URL, query string, fragmento, cookie, log nem
mensagem de erro.

### 2.3 Cookies e `Referer` no handoff

- O formulário é gerado pelo HUB e **deliberadamente não transporta**
  cookies do HUB, token CSRF, dados de sessão nem parâmetros adicionais:
  o único campo é `code`.
- O HUB **não controla** o que o navegador envia por conta própria. O
  navegador pode enviar cookies que ele já tenha associados à **origem de
  destino** (cookies do próprio produto, conforme seus atributos). Isso não
  é uma propriedade do HUB e não deve ser lido como "o `POST` nunca contém
  `Cookie`".
- Cookies são escopados por **host**, não por porta. Um produto servido no
  mesmo host do HUB (por exemplo, o mesmo nome de host em outra porta)
  poderia receber cookies aplicáveis ao destino, inclusive o de sessão do
  HUB, conforme host/domínio, `Path`, `Secure` e `SameSite` do cookie. Por
  isso, em produção, o HUB e os produtos devem estar em hosts distintos.
- **Requisito de deploy:** a sessão do HUB deve permanecer *host-only*. O HUB
  não define `SESSION_COOKIE_DOMAIN` (o padrão do Flask é *host-only*), e o
  cookie usa `HttpOnly` e `SameSite=Lax` (`Secure` em staging/produção,
  `app/config.py`). O deploy **não deve** configurar um `Domain` amplo que
  alcance os produtos.
- O `Referrer-Policy: no-referrer` da página de handoff instrui o navegador a
  omitir o `Referer`. É uma instrução do HUB ao navegador, não uma garantia
  do HUB sobre o destino.
- **Evidência limitada:** na homologação #72, o receptor controlado
  (`http.server` local) não registrou `Cookie`, `Referer`, query string nem
  token CSRF. Esse resultado vale para aquele ambiente: HUB em `127.0.0.1` e
  destino em `localhost` (hosts diferentes, portanto sem cookie compartilhado).
  Não é uma propriedade universal do navegador.

### 2.4 URL de destino (`destination_url`)

`OrganizationProductInstallationService.validate_installation_url` é aplicada
na escrita administrativa (`configure_installation`) e novamente na emissão
(`issue_launch_code`). Ela rejeita:

- esquema diferente de `https`/`http`;
- **query string ou fragmento não vazios** (`parts.query != ""` e
  `parts.fragment != ""`). Um `?` ou `#` final vazio é aceito pelo validador,
  porque `urlsplit` o interpreta como vazio;
- usuário ou senha na URL, host ausente ou não ASCII, espaço no host,
  caracteres de controle e `\`;
- URL com mais de 255 caracteres;
- `http` em produção (`IS_PRODUCTION`). Fora de produção, `http` só é aceito
  para `localhost`, `127.0.0.1`, `::1` e hosts `.local`;
- em produção, host/porta fora do host canônico do produto
  (`L_GEDO_URL`, `L_KALENDER_URL`, `L_HUNT_URL`), aceitando o próprio host ou
  um subdomínio.

A URL é guardada e devolvida exatamente como recebida (sem normalização) e o
handoff não acrescenta parâmetros por conta própria.

### 2.5 O código

- Opaco e aleatório: `secrets.token_urlsafe(32)`, hoje com 43 caracteres do
  alfabeto URL-safe. **O consumidor deve tratá-lo como opaco** e não depender
  do tamanho exato: o HUB aceita até 256 caracteres na apresentação.
- Guardado no banco somente como hash SHA-256 (`product_launch_codes.code_hash`,
  único). O valor em claro existe transitoriamente no retorno do service
  emissor e na página/formulário de handoff, permanecendo fora do banco e dos
  logs.
- Vinculado à **instalação** (não a uma credencial específica) e ao usuário.
- Expiração (`expires_at`) = instante de emissão + `PRODUCT_LAUNCH_CODE_TTL_SECONDS`.
  Uso único.

## 3. Etapa 2: consumo servidor-a-servidor

### 3.1 Requisição

`POST /api/federation/launch/consume` (`app/blueprints/federation.py`; isento
de CSRF porque não usa cookie de sessão).

- `Content-Type: application/json`; corpo `{"code": "<código>"}`, objeto JSON
  com `code` string não vazia de até 256 caracteres; corpo de até 4096 bytes.
- Autenticação **HTTP Basic** da instalação:
  - *username* = `installation_public_id` (UUID da instalação, o mesmo valor
    devolvido no campo `installation_public_id` da resposta de sucesso);
  - *password* = segredo da credencial da instalação;
  - o header HTTP Basic é `Authorization: Basic <BASE64(username:password)>`,
    isto é, a codificação Base64 do texto `username:password`.
- O segredo nunca deve ir em linha de comando, URL, log nem arquivo
  versionado. Este documento não traz nenhum exemplo funcional de credencial.
- Métodos `GET`, `PUT`, `PATCH` e `DELETE` são registrados na rota apenas para
  devolver `405` no mesmo contrato JSON.

### 3.2 Ordem de validação

1. Método diferente de `POST` → `405`.
2. Tamanho do corpo, `Content-Type` JSON, JSON objeto e `code` válido → `400`
   (**antes** da autenticação: um `400` não revela nada sobre credenciais).
3. Cabeçalho `Authorization` ausente, de outro esquema, ou sem usuário ou
   senha → `401`.
4. `ProductLaunchCodeService.consume_launch_code`: autentica a instalação e
   consome o código; qualquer rejeição → `401`. Falha operacional ou exceção
   inesperada → `500`.

### 3.3 Respostas implementadas hoje

Todas são geradas pela função `_no_store_response` da rota e carregam
`Cache-Control: no-store` e `Pragma: no-cache`.

| Status | Corpo | Quando | Verificação automatizada (`tests/test_federation_launch_consume.py`) |
|---|---|---|---|
| `200` | JSON de sucesso (seção 3.4) | Código válido, instalação autenticada, revalidação aprovada | `test_success_response_has_no_store_cache_headers` |
| `400` | `{"error": "invalid_request"}` | Corpo grande demais, `Content-Type` não JSON, JSON inválido ou não objeto, `code` ausente, vazio, não string ou acima de 256 | `test_400_response_has_no_store_cache_headers` |
| `401` | `{"error": "invalid_credentials_or_code"}` | Sem `Authorization`, credencial inválida, código inexistente, expirado, já consumido, de outra instalação, ou autorização revogada após a reivindicação (Caminho C). Todas indistinguíveis. Nunca envia `WWW-Authenticate` | `test_401_response_has_no_store_cache_headers`; ausência de `WWW-Authenticate` |
| `405` | `{"error": "invalid_request"}` | `GET`, `PUT`, `PATCH`, `DELETE`. Com `Cache-Control: no-store` e `Pragma: no-cache`, sem `WWW-Authenticate`. O método não chega ao service nem ao banco | `test_disallowed_methods_return_full_json_contract`, `..._never_reach_the_service`, `..._have_no_database_effect` |
| `500` | `{"error": "internal_error"}` | Falha operacional ou exceção inesperada durante o consumo | `test_500_response_has_no_store_cache_headers` |

Sobre o `500`: o corpo é genérico, sem traceback nem detalhe interno; o
detalhe técnico vai apenas para o log do servidor. Uma exceção fora dos
blocos tratados da rota cairia no tratamento padrão do Flask; por isso o modo
`DEBUG` nunca deve ficar ativo fora do desenvolvimento local.

As garantias de `no-store` e `no-cache` acima valem para as respostas
produzidas por esta rota, conforme o código e os testes citados. Elas não
foram verificadas para respostas geradas fora dela.

### 3.4 Resposta de sucesso (`200`)

`ProductLaunchAuthorization`, serializada com `dataclasses.asdict`. O
conjunto de chaves é exatamente este, e é verificado por
`test_valid_request_returns_200_with_exact_contract_fields` e por
`tests/test_product_launch_code_service.py`.

| Campo | Tipo JSON | Semântica e invariantes |
|---|---|---|
| `contract_version` | inteiro | Versão do contrato. Hoje `1`. Só é incrementado em mudança incompatível de formato. |
| `issuer` | string | Constante `"hub.licilink"` (`_CONTRACT_ISSUER`). Nunca derivada de configuração. |
| `authorization_id` | string (UUID) | Identificador da linha do código em `product_launch_codes` (**não** é o código nem o hash). Identifica unicamente a autorização. |
| `sub` | string (UUID) | `User.id` do HUB. Identificador estável e imutável do usuário entre sistemas. O e-mail nunca é chave de identidade. |
| `email` | string | E-mail do usuário no momento do consumo. Informativo. Uma resposta `200` só ocorre se o usuário está ativo e tem e-mail verificado nessa validação. |
| `name` | string | Nome do usuário no momento do consumo. Informativo. |
| `organization_id` | string (UUID) | Organização da assinatura vinculada à instalação. |
| `product_code` | string | Código canônico do produto da instalação (`kalender`, `gedo` ou `hunt`). |
| `installation_public_id` | string (UUID) | `public_id` da instalação autenticada (o mesmo usado como *username* do Basic). |
| `role` | string | Nome do papel do usuário no vínculo ativo com a organização, **no instante da validação do consumo** (hoje `owner` ou `member`). O mapeamento desse papel para permissões locais no GEDO está **pendente**. O `owner` do HUB **não** deve implicar automaticamente `staff`/administrador interno no GEDO. |

Nenhum campo carrega código, hash ou segredo.

**Contrato minimizado (decisão D-1/D-2 da #62).** O contrato V1 não inclui
`issued_at`, `expires_at`, status da assinatura ou da instalação, nem indicador
explícito de e-mail verificado. A expiração pertence ao **código, antes do
consumo**, e não à autorização devolvida depois dele. Depois de um consumo
válido, o TTL do código deixa de limitar a autorização devolvida pelo HUB. A
duração, a renovação e o encerramento da sessão local são responsabilidade do
GEDO e **ainda precisam ser confirmados**.

### 3.5 Como o consumo decide

`consume_launch_code` autentica a instalação e só depois executa uma única
instrução `UPDATE` condicional (hash do código, mesma instalação, `consumed_at`
nulo, `expires_at` no futuro). Três caminhos de rejeição, todos com o mesmo `401`:

- **Caminho A:** credencial inválida (inclusive instalação inativa ou sem
  credencial aceita). Nenhum código de lançamento é consultado ou alterado, e
  nenhuma auditoria de domínio é gerada. A autenticação consulta a instalação
  e suas credenciais.
- **Caminho B:** o `UPDATE` não afeta nenhuma linha (código inexistente,
  expirado, já consumido ou de outra instalação). Nada é alterado.
- **Caminho C:** o código foi reivindicado (`consumed_at` gravado), mas a
  revalidação seguinte (usuário, e-mail verificado, assinatura
  `active`/`trial`, organização, vínculo, instalação) falhou. O código fica
  consumido, nenhuma identidade é devolvida.

**Decisão D-1: rejeições não geram auditoria de domínio.** Credencial
inválida, código inexistente, expirado, reutilizado, instalação divergente e
Caminho C **não** gravam eventos em `audit_logs`. Isso preserva as decisões e
os testes das #66, #67 e #71. Só o consumo válido grava
`organization_product_installation.launch_code_consumed`, e a emissão grava
`organization_product_installation.launch_code_issued`. Rejeições poderão, no
futuro, produzir logs ou métricas operacionais sanitizados e sujeitos a rate
limiting, mas não auditoria de domínio.

**Uso único e TTL.** O código só pode ser consumido uma vez, e só antes de
`expires_at`. O padrão é 60 s (`PRODUCT_LAUNCH_CODE_TTL_SECONDS`).

## 4. Decisões aprovadas e o que ainda não existe

| Tema | Decisão | Estado hoje |
|---|---|---|
| **TTL (D-3)** | Em produção, entre 30 e 60 s, padrão 60 s. Valores menores só em testes. | Padrão 60 s. `ProductLaunchCodeService._resolve_ttl_seconds` aceita de 1 a 300 s em qualquer ambiente. **A validação por ambiente ainda não existe.** Ela caberia em `_resolve_ttl_seconds` (com a leitura de `IS_PRODUCTION`) ou na validação pós-configuração de `app/config.py`, a definir em trabalho futuro. |
| **HTTPS (D-4)** | O TLS termina no proxy/deploy. O HUB deverá validar o esquema seguro em produção, com configuração explícita de proxies confiáveis. `POST` inseguro **não** deve ser redirecionado, e sim rejeitado de forma genérica. | **Não implementado.** O HUB não adiciona `ProxyFix`, não força HTTPS nem HSTS no endpoint. Só exige HTTPS na **URL da instalação** em produção. |
| **Rate limiting (D-5)** | Sem armazenamento em memória em produção. Backend compartilhado, de preferência Redis. Limite por IP antes da autenticação e limite por instalação **somente depois** de autenticação válida, para não permitir bloqueio direcionado por `public_id` forjado. | **Não implementado** no consumo nem na emissão. O Flask-Limiter só protege as rotas de autenticação de usuário (`auth.py`). |
| **Resposta `429`** | Planejada. | **Planejada, ainda não implementada.** O corpo e os headers ainda serão definidos e deverão seguir o mesmo contrato de resposta genérica, sem revelar credenciais ou códigos. |

## 5. Evidências

- **Testes automatizados do lado HUB:** `tests/test_federation_launch_consume.py`
  (contrato HTTP do consumo), `tests/test_product_launch_code_service.py`
  (emissão e consumo), `tests/test_launcher_launch_route.py` e
  `tests/test_launcher_launch_handoff.py` (rota e handoff),
  `tests/test_organization_product_installation*.py` e
  `tests/test_admin_org_product_installation.py` (instalação e URL).
- **Homologação #72 (lado HUB, receptor controlado, sem envolver o GEDO):**
  aprovada com ressalvas. Cobriu consumo válido, uso único, expiração, código
  inexistente, credencial ausente ou incorreta, isolamento entre instalações,
  Caminho C e métodos `405`. Nenhuma dessas evidências prova o comportamento do
  GEDO.

## 6. Como evoluir o contrato

Qualquer mudança no formato da resposta de sucesso, nos códigos de status, nos
corpos de erro ou no transporte do handoff exige, no mesmo Pull Request:
atualizar este documento, atualizar os testes de contrato existentes (em
especial `_EXPECTED_FIELDS` em `tests/test_federation_launch_consume.py`) e
avaliar `_CONTRACT_VERSION`. Uma mudança do formato de handoff (`POST`,
`application/x-www-form-urlencoded`, campo `code`) deve ser coordenada e
versionada nos dois lados (HUB e GEDO), nunca decidida unilateralmente.

## 7. Trabalhos futuros, em Issues separadas

- Corrida em `PendingEmailVerification` (independente da integração).
- Rate limiting federado, com backend compartilhado.
- CLI administrativa de credenciais.
- Validação HTTPS em produção, com proxies confiáveis explícitos.
- Validação do TTL por ambiente.
- Teste de concorrência real em PostgreSQL.
- Disponibilização, confirmação e integração do lado GEDO (associação de
  identidade, cliente servidor-a-servidor, rota de entrada, sessão local) e a
  homologação integrada HUB ↔ GEDO.
