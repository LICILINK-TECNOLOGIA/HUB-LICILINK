# Credenciais de instalação (provisionamento, emissão, rotação e revogação)

Este documento descreve como uma credencial de instalação é provisionada,
emitida, rotacionada e revogada **hoje**, usando somente as APIs que existem
no HUB: `InstallationCredentialService`
(`app/services/installation_credential_service.py`, Issue #64). Não existe
rota HTTP, tela administrativa nem comando CLI para essas operações. O
procedimento descrito aqui é **provisório** e usa `flask shell`.

Complementa [`federation-contract.md`](federation-contract.md), que descreve o
contrato do endpoint de consumo. Nada aqui define comportamento do GEDO.

## 1. Modelo e regras

- Cada `OrganizationProductInstallation` (uma por par organização/produto)
  tem seu próprio `public_id` (UUID, não secreto) e suas próprias credenciais
  em `organization_product_installation_credentials`.
- O segredo é gerado com `secrets.token_urlsafe` (CSPRNG) e devolvido em claro
  **uma única vez**, no retorno de `issue_credential` ou `rotate_credential`.
  O banco guarda apenas o hash SHA-256 (`secret_hash`).
- Autenticação (`authenticate_installation(public_id, segredo)`): exige
  instalação **ativa**, `public_id` válido e uma credencial aceita; compara
  os hashes com `hmac.compare_digest`. Qualquer falha devolve `None`, sem
  distinguir a causa. Ela **não** consulta o status da assinatura
  (`OrganizationProduct`): a revogação do acesso ao produto atua na
  revalidação do consumo (Caminho C, ver o contrato).
- O segredo nunca é gravado em log nem em auditoria. O evento registra apenas
  identificadores (`credential_id`, `previous_credential_id`,
  `new_credential_id`, `revoked_credential_ids`).
- Nunca reutilize `SECRET_KEY`, `HUB_API_KEY`, chaves SSH ou qualquer outra
  credencial existente como segredo de instalação.

### 1.1 Quantas credenciais são aceitas

Uma credencial é **aceita** quando `revoked_at` é nulo (a atual) ou está no
futuro (a anterior, dentro da janela de rotação).

| Situação | Credenciais aceitas |
|---|---|
| Normal | A credencial atual. |
| Durante a janela de rotação (padrão 300 s, `INSTALLATION_CREDENTIAL_ROTATION_GRACE_SECONDS`) | A nova (atual) e a anterior. Nunca mais de duas. |
| Depois da janela de rotação | Somente a atual. |
| Depois de `revoke_credential` | Nenhuma. |
| Instalação nunca provisionada | Nenhuma. |

`issue_credential` recusa a emissão enquanto houver **qualquer** credencial
aceita, inclusive uma que esteja só na janela de rotação. Para substituir uma
credencial existente, use a rotação.

### 1.2 Efeito sobre códigos de lançamento já emitidos

O código de lançamento é vinculado à **instalação**, não a uma credencial
específica (`product_launch_codes.organization_product_installation_id`). As
operações de credencial **não alteram** nenhuma linha de `product_launch_codes`.

- **Revogação:** a credencial revogada deixa de autenticar. Sem nenhuma
  credencial aceita, uma tentativa de consumo falha na autenticação (Caminho A,
  `401`) **antes** de qualquer `UPDATE`; portanto os códigos já emitidos **não
  são marcados como consumidos** por causa da revogação. Eles não podem ser
  consumidos com a credencial revogada, permanecem elegíveis até expirarem e,
  se uma nova credencial for emitida para a mesma instalação antes da
  expiração, poderiam ser consumidos com ela.
- **Rotação:** um código emitido antes da rotação pode ser consumido com a
  nova credencial e, durante a janela, com a anterior.
- **Instalação desativada:** também cai na falha de autenticação (Caminho A) e
  não consome o código.

## 2. Procedimento provisório (`flask shell`)

**Requisitos e cuidados.** Execute somente em um ambiente administrativo
controlado, em um terminal privado, com o mesmo `.env`/configuração do
ambiente que será operado (nunca contra o banco de outro ambiente). Ambiente
de desenvolvimento local segue o guia do [`README.md`](../README.md); as regras
de ambiente de `docs/admin-cli.md` valem aqui também.

**O segredo.** Só o retorno de `issue_credential`/`rotate_credential` o
contém. Sempre atribua o retorno a uma variável (o console interativo exibe
o valor de qualquer expressão que não seja atribuída). Exiba-o **uma única
vez**, copie-o diretamente para o canal seguro combinado com quem opera o
produto e depois apague a variável. Essa exibição única deve ocorrer em um
terminal administrativo controlado, sem gravação de sessão, transcrição,
compartilhamento de tela ou captura automática de saída. Nunca digite ou cole
o segredo em um comando (ele iria para o histórico do terminal) e não o grave
em arquivo.

Os identificadores abaixo são **fictícios**. Substitua-os pelos valores reais
do ambiente.

### 2.1 Localizar a instalação

O `installation_id` e o `public_id` não aparecem na tela administrativa
atual. Obtenha-os pelo shell (não são segredos):

```python
import uuid
from app.models import Product
from app.services.organization_product_installation_service import (
    OrganizationProductInstallationService,
)

org_id = uuid.UUID("00000000-0000-4000-8000-000000000001")  # FICTÍCIO
product = Product.query.filter_by(code="gedo").first()
installations = OrganizationProductInstallationService.list_installations_by_organization(org_id)
installation = installations.get(product.id)
print(installation.id, installation.public_id, installation.is_active)
```

Se `installation` for `None`, a organização ainda não tem instalação
configurada para esse produto (configure-a pela tela administrativa antes).
A instalação precisa estar **ativa** para autenticar. O `public_id` é o
*username* do HTTP Basic do consumo. O `installation_id` (`installation.id`) é
o parâmetro das operações abaixo.

### 2.2 Emitir a primeira credencial

```python
from app.services.installation_credential_service import InstallationCredentialService

installation_id = "00000000-0000-4000-8000-000000000002"  # FICTÍCIO
actor_user_id = "00000000-0000-4000-8000-000000000003"    # FICTÍCIO: admin interno responsável

secret = InstallationCredentialService.issue_credential(installation_id, actor_user_id)
print(secret)   # única exibição: copie para o canal seguro combinado
del secret
```

Grava o evento `organization_product_installation.credential_issued`. Se a
instalação já tiver uma credencial aceita, levanta `InstallationCredentialError`
("Esta instalação já possui uma credencial aceita. Use a rotação para
substituí-la."), sem alterar nada.

### 2.3 Rotacionar

```python
new_secret = InstallationCredentialService.rotate_credential(installation_id, actor_user_id)
print(new_secret)   # única exibição
del new_secret
```

A credencial atual continua aceita por `INSTALLATION_CREDENTIAL_ROTATION_GRACE_SECONDS`
(padrão 300 s). Quem opera o produto precisa trocar o segredo dentro dessa
janela; depois dela, só a nova é aceita. Uma rotação iniciada durante uma
janela aberta a encerra imediatamente. Grava
`organization_product_installation.credential_rotated`. Sem credencial ativa, a
operação é recusada ("Emita uma credencial primeiro.").

### 2.4 Revogar

```python
InstallationCredentialService.revoke_credential(installation_id, actor_user_id)
```

Revoga **imediatamente** todas as credenciais aceitas da instalação, inclusive
uma que ainda esteja em janela de rotação. Não apaga linhas. Grava
`organization_product_installation.credential_revoked`. Sem nenhuma credencial
aceita, a operação é recusada. Use-a também como resposta a suspeita de
vazamento; depois disso, o consumo passa a responder `401` até uma nova
emissão (seção 1.2).

### 2.5 Erros

- `InstallationCredentialError`: erro de domínio esperado, com mensagem curada
  (instalação não encontrada, credencial já aceita, nada a rotacionar ou
  revogar).
- `InstallationCredentialOperationError`: falha inesperada; mensagem genérica,
  nenhuma alteração salva. A causa técnica fica na exceção encadeada.

Em qualquer erro a transação é revertida e nenhum segredo é devolvido.

### 2.6 Conferir o resultado sem expor o segredo

- Não chame `authenticate_installation` no shell com o segredo (ele iria para
  o histórico). A verificação de ponta a ponta é o próprio consumo feito pelo
  cliente do produto, quando ele estiver disponibilizado e confirmado para a
  integração.
- A auditoria pode ser consultada sem segredo:

```python
from app.models import AuditLog

AuditLog.query.filter(
    AuditLog.resource_id == installation.id,
    AuditLog.action.like("organization_product_installation.credential_%"),
).order_by(AuditLog.created_at).all()
```

## 3. Ambientes

Cada ambiente (desenvolvimento, homologação, produção) tem seu próprio banco
e portanto suas próprias credenciais. Os segredos **não** devem ser copiados
entre ambientes, e cada instalação de cada produto tem o seu. Um vazamento em
um ambiente ou em uma instalação não deve afetar os demais.

## 4. Lacunas administrativas conhecidas

Estas lacunas são conhecidas e não são defeitos da versão atual:

- Não há rota HTTP nem comando CLI para emitir, rotacionar ou revogar; só o
  `flask shell` descrito acima.
- O `public_id` e o `installation_id` não aparecem na interface
  administrativa.
- `actor_user_id` é apenas registrado na auditoria. O serviço **não** valida
  se o usuário é administrador interno; o controle é operacional.
- Não existe canal embutido para entregar o segredo a quem opera o produto; a
  entrega segura é responsabilidade do operador.
- Não há renovação automática: a rotação é sempre manual e a janela padrão é
  curta (300 s).
- Não há rate limiting no endpoint de consumo (ver o contrato).

Evolução planejada, em Issues separadas: CLI administrativa de credenciais,
rate limiting federado com backend compartilhado e validação HTTPS em
produção.
