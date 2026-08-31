# Administrador interno x membro de organização

Decisão de produto (Issue #19): um usuário com `User.is_internal_admin=True`
**nunca** pode ter um vínculo ativo como `OrganizationMember` de uma
organização cliente - nem como `owner`, nem como `member`. A administração
interna do LiciLink é um mecanismo **completamente separado** das
permissões organizacionais.

## As duas coisas nunca se misturam

- **`User.is_internal_admin`** controla acesso às rotas administrativas
  internas (`/admin/*`, via `internal_admin_required`) - não tem nenhuma
  relação com `Role`/`OrganizationMember`.
- **`OrganizationMember.role` (`owner`/`member`)** controla o que uma
  pessoa pode fazer **dentro de uma organização cliente** - nunca concede
  nem depende de acesso administrativo interno.

`is_internal_admin` nunca funciona como bypass: não concede acesso
organizacional automático, e um vínculo de organização (mesmo que
existisse) nunca concederia acesso às rotas internas. São dois sistemas de
autorização independentes.

## Onde a regra é aplicada

A política central usa duas peças, ambas em `OrganizationService`, para
nunca duplicar a decisão `is_internal_admin` em vários pontos:

- **`_user_is_internal_admin(user_id)`** - predicado único: verdadeiro se
  o usuário existir e for administrador interno. É a ÚNICA função que lê
  `User.is_internal_admin` para esta política - todo o resto reutiliza seu
  resultado, nunca reimplementa a checagem.
- **`_reject_internal_admin_as_member(user_id)`** - validador que usa o
  predicado acima e levanta `ValueError` - chamado como a primeira
  validação (antes de qualquer lock, mutação, `Role`/`OrganizationMember`/
  `AuditLog` ou commit) em:
  - `add_member` - rejeita a criação de um novo vínculo;
  - `_apply_status_transition`, quando a transição leva para `active` -
    rejeita `reactivate_member` (suspended → active) e
    `restore_removed_member` (removed → active) sobre um vínculo legado;
  - `change_member_role` - rejeita promover/rebaixar/reorganizar o papel
    de um vínculo (mesmo legado), pois isso o trataria como legítimo.

A rejeição acontece **antes** de qualquer gravação - inclusive contra um
POST enviado diretamente à rota, ignorando a interface por completo.

Consultas (que precisam devolver "nada", não levantar erro) reutilizam o
mesmo predicado `_user_is_internal_admin`, nunca o validador:

- **`get_active_membership`** retorna `None` (contrato já existente,
  preservado sem mudança de tipo/assinatura) se o usuário do vínculo for
  administrador interno, mesmo com a linha `active` no banco. Como é o
  único portão usado por `AccessService.get_organization_products`, o
  acesso a produtos é bloqueado automaticamente, sem nenhuma alteração em
  `AccessService` - e sem risco de exceção não tratada, porque o
  `ValueError` que `AccessService` já levanta para "sem vínculo ativo" é
  exatamente o mesmo caminho usado para este caso.
- **`get_user_organizations`** retorna lista vazia para administrador
  interno, mesmo com um vínculo `active` legado. Esta função é a que
  `dashboard.index()` usa para escolher a organização "atual" do usuário -
  sem esta exclusão, uma organização legada chegaria a ser selecionada
  como `current_org`, e a chamada seguinte a
  `AccessService.get_organization_products` levantaria `ValueError` **sem
  nenhum tratamento na rota**, resultando em HTTP 500. Com a exclusão,
  `current_org` é `None` e o dashboard renderiza normalmente o estado "sem
  vínculo" já suportado pela interface.

## Vínculos legados (anteriores a esta política)

Um vínculo pré-existente de administrador interno **não é apagado nem
alterado automaticamente** por esta mudança. Comportamento:

- **Não pode mais ser reativado/restaurado/ter o papel alterado**:
  `reactivate_member`, `restore_removed_member` e `change_member_role`
  passam a rejeitar essas operações.
- **Não concede mais acesso**, mesmo estando `active`: nem via
  `AccessService` (produtos), nem aparecendo em `/dashboard` como
  organização do usuário.
- **Pode ser suspenso ou removido normalmente** - suspensão/remoção não
  passam pela política acima, então um operador sempre consegue
  neutralizar/sanear um vínculo legado inválido sem ficar bloqueado.
- A proteção de "a organização precisa ter ao menos um OWNER ativo" (Issue
  #15) continua valendo normalmente. Se o vínculo legado inválido for o
  único OWNER ativo da organização, é preciso atribuir um OWNER legítimo
  antes de suspender/remover o vínculo do administrador interno - o mesmo
  procedimento de duas etapas já exigido para trocar qualquer último
  OWNER, não um estado impossível de corrigir.
- As rotas `/admin/*` (`internal_admin_required`) continuam funcionando
  normalmente para o administrador interno - elas nunca dependeram de
  `OrganizationMember`.

## Interface

O seletor "Usuário" em `admin/org_details.html` já excluía administradores
internos (`{% if not u.is_internal_admin %}`) - isso é preservado como
conveniência de UX, mas nunca foi (e continua não sendo) o mecanismo de
segurança: a regra real está no backend, então mesmo ignorando o template
por completo (POST forjado) a operação é rejeitada.
