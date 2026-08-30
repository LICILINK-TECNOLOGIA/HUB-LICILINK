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

## Diferença entre bootstrap estrutural e dados de demonstração

Este comando cuida apenas do **catálogo estrutural mínimo** (papéis e
produtos) - o vocabulário que o resto do sistema espera encontrar. Ele não
substitui `seed_data.py` (dados de demonstração: usuários, organizações e
concessões de produto de exemplo) nem depende dele. `seed_data.py`
permanece fora do escopo deste comando e não deve ser executado como parte
deste bootstrap.
