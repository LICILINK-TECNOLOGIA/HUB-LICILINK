# Bootstrap estrutural (CLI)

`flask bootstrap-structural-data` garante a existência do catálogo
estrutural mínimo do HUB LiciLink: os **papéis** (`owner`, `member`) e os
**produtos** (`kalender`, `gedo`, `hunt`) usados pelo restante do sistema
(seleção de papel ao adicionar um membro, concessão de acesso a produto por
organização, etc.).

## Quando executar

Execute este comando uma vez ao preparar uma instalação nova (banco vazio),
antes de usar telas ou fluxos que dependam de `Role`/`Product` já existirem
(por exemplo, o seletor de "Papel" ao adicionar um membro a uma organização,
ou `AccessService.grant_product_access`). Também é seguro executá-lo a
qualquer momento depois - inclusive em produção, sobre um banco já em uso -
sem risco de duplicar ou corromper dados, porque é **idempotente**.

## Códigos canônicos dos produtos (Issue #27)

`Product.code` persistido é sempre `kalender`, `gedo` ou `hunt` - sem o
prefixo `L-`. O prefixo `L-` pertence exclusivamente ao nome comercial
(`Product.name`, ex.: "L-Kalender") e às variáveis de configuração
(`L_KALENDER_URL`, `L_GEDO_URL`, `L_HUNT_URL`), nunca ao código persistido.
`STRUCTURAL_PRODUCTS` (`app/services/bootstrap_service.py`) é a **única**
fonte canônica desses três códigos - `AccessService` e o dashboard nunca
redeclaram uma lista própria, apenas leem os registros já existentes no
banco. `hunt` é um produto estrutural igual aos demais, sem nenhum
tratamento especial. Não existem aliases (`l-kalender`, `l-gedo`,
`l-hunt`) neste catálogo: não há, no código atual, nenhum caminho que
persista produtos com essa grafia, e nenhuma ocorrência funcional dela foi
encontrada neste repositório - portanto nenhuma migration ou alias é
necessário com base no código e nos testes auditados (Issue #27; bancos
PostgreSQL e ambientes externos não foram auditados nesta Issue). Se um
ambiente externo tiver dados criados fora dos caminhos deste repositório
(por exemplo, por inserção manual), isso deve ser auditado separadamente
antes de qualquer correção de dados.

## O que ele cria - e o que ele NÃO cria

Cria **apenas** os dois catálogos estruturais:

- `Role`: `owner`, `member`
- `Product`: `kalender`, `gedo`, `hunt` (nome, descrição e URL oficiais já
  definidos no projeto - a URL de cada produto vem da configuração de
  ambiente já existente em `app/config.py`, nunca de um valor inventado)

Ele **não cria**:

- usuários (`User`);
- organizações (`Organization`) ou vínculos pessoa-organização
  (`OrganizationMember`);
- permissões (`Permission`) ou vínculos produto-permissão
  (`ProductPermission`);
- concessão/contrato de produto para nenhuma organização
  (`OrganizationProduct`) - isso é uma decisão comercial/administrativa
  separada, feita via `AccessService.grant_product_access`;
- senhas, tokens, chaves ou qualquer credencial.

## Segurança, idempotência e conflitos

```
flask bootstrap-structural-data
```

- **Idempotente quando não há conflito**: rodar novamente não duplica
  nada. Cada papel/produto é identificado pelo seu campo único
  (`Role.name` / `Product.code`); se já existir com os metadados
  canônicos (description; para produto também name e a URL do ambiente
  atual), nada é recriado - o sucesso significa que todo o catálogo
  estrutural está presente e compatível.
- **Bloqueante em conflito, sem sucesso parcial**: se QUALQUER
  papel/produto já existir com o mesmo identificador mas metadados
  diferentes dos oficiais (por exemplo, uma descrição editada
  manualmente, ou uma URL de um ambiente anterior), o comando:
  - **não altera** o registro em conflito;
  - **não cria nenhum** dos registros ausentes, mesmo os que não têm
    conflito próprio (ex.: um conflito em `owner` também impede a criação
    do `Product` `hunt`, mesmo que `hunt` estivesse totalmente ausente e
    sem nenhuma divergência);
  - **não executa nenhum commit** - toda a validação (leitura) acontece
    antes de qualquer gravação, e a operação inteira é abortada assim que
    o primeiro conflito é encontrado em qualquer parte do catálogo;
  - **encerra com código de saída diferente de zero**;
  - informa, na saída, apenas os **identificadores** em conflito (nome do
    papel / código do produto) - nunca a URL completa, descrição ou
    qualquer outro conteúdo do registro divergente.
  - Este comando **nunca reconcilia nem sobrescreve** um registro
    existente automaticamente - a divergência deve ser resolvida
    conscientemente pelo operador (ajustando o registro no banco ou o
    catálogo, conforme o caso) antes de rodar o comando de novo.
- **Transação única**: a fase de validação é somente leitura (nenhum
  `db.session.add`/`commit`); se não houver conflito, todos os registros
  ausentes são gravados por um único `db.session.commit()`. Qualquer falha
  durante essa gravação reverte (`rollback`) tudo por completo - nunca
  fica um catálogo parcialmente criado.
- **URL sempre resolvida em tempo de execução**: a URL de cada produto é
  lida de `current_app.config` no momento da execução do comando (nunca
  de um valor fixo gravado no catálogo ou lido da classe de configuração
  no import do módulo) - reflete sempre o ambiente em que o comando está
  rodando no momento.
- **Não roda sozinho**: o comando é registrado no Flask CLI, mas só é
  executado quando alguém chama `flask bootstrap-structural-data`
  explicitamente - não faz parte da inicialização automática da aplicação.

## `flask bootstrap-structural-data` é o único mecanismo oficial

Este é o **único** comando suportado para criar o catálogo estrutural
mínimo (papéis e produtos) do HUB LiciLink - não há script alternativo de
"seed"/dados de demonstração no projeto, e nenhum é necessário. Ele cria
somente os papéis e produtos listados acima; dados fictícios (usuários,
organizações, senhas de exemplo) nunca fazem parte do bootstrap de
produção, nem deste comando nem de nenhum outro.

Para os demais recursos, cada um tem seu próprio fluxo oficial, nenhum
deles sobreposto por este comando:

- **Administradores internos**: `flask create-admin` (ver
  [`docs/admin-cli.md`](./admin-cli.md)) - prompt interativo com senha
  oculta, nunca senha fixa no código.
- **Usuários finais**: fluxo público normal de cadastro e verificação de
  e-mail (`AuthService.start_registration`/`verify_email`).
- **Organizações e vínculos** (`Organization`, `OrganizationMember`): telas
  e serviços administrativos próprios do sistema (`OrganizationService`),
  nunca por um script de bootstrap.

`flask bootstrap-structural-data` continua **idempotente** quando o
catálogo existente já é compatível com o canônico, e os **conflitos
continuam bloqueantes e atômicos** (ver seção acima) - nenhuma mudança de
comportamento em relação ao que já está documentado neste arquivo.
