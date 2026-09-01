from dataclasses import dataclass
from typing import Optional

from ..extensions import db
from ..models import Organization, OrganizationProduct, Product
from .audit_service import AuditService
from .bootstrap_service import STRUCTURAL_PRODUCTS
from .organization_service import OrganizationService

# Issue #30: mesma fonte canônica única da Issue #27 - nunca redeclarar
# códigos de produto aqui. Um product_code só é aceito por
# grant_product_access/revoke_product_access se pertencer a este conjunto,
# mesmo que exista algum Product com outro código no banco.
_CANONICAL_PRODUCT_CODES = {spec["code"] for spec in STRUCTURAL_PRODUCTS}


class ProductAccessError(ValueError):
    """Erro de domínio esperado e seguro (organização inexistente, código
    de produto não canônico, produto canônico ainda ausente do banco,
    etc.) - a mensagem já é curada para ser exibida diretamente ao
    operador, nunca contém detalhe de banco/driver. Continua sendo um
    `ValueError` (compatibilidade com `pytest.raises(ValueError)` já
    usado pelos chamadores), mas nunca é a mesma classe usada para uma
    falha inesperada - ver `ProductAccessOperationError`."""


class ProductAccessOperationError(ValueError):
    """Falha inesperada ao processar a operação (banco, driver, AuditLog,
    ou qualquer exceção não prevista) - deliberadamente NÃO é subclasse de
    `ProductAccessError` (são classes irmãs), para que a rota consiga
    capturar uma sem capturar a outra. A mensagem pública desta exceção é
    sempre genérica; a causa técnica real é preservada em `__cause__` via
    `raise ... from exc`, nunca exposta ao usuário - só para quem
    inspecionar/logar a exceção no servidor."""


@dataclass(frozen=True)
class ProductAccessResult:
    """Resultado de grant_product_access/revoke_product_access.

    `organization_product` é `None` somente no caso de revogar um vínculo
    que nunca existiu (no-op sem criar linha). `changed` distingue uma
    mutação real (linha criada ou status alterado, com AuditLog) de uma
    operação idempotente (nenhuma escrita, nenhum AuditLog) - permite à
    rota mostrar uma mensagem diferente sem inspecionar timestamps.
    """
    organization_product: Optional[OrganizationProduct]
    changed: bool


class AccessService:
    @staticmethod
    def get_organization_products(user_id, organization_id):
        """
        Retorna todos os produtos disponíveis no sistema e o status deles
        para a organização especificada. Usado para montar o Launcher.

        Portão de autorização obrigatório: exige `user_id` e confirma, a
        cada chamada, que o usuário possui vínculo ATIVO com a organização
        (via `OrganizationService.get_active_membership`) antes de consultar
        ou liberar qualquer produto. Nunca confia em `organization_id` vindo
        de sessão, URL ou formulário sem essa revalidação - vínculo
        inexistente, suspenso ou removido nega o acesso aqui, no ponto
        central, independentemente de quem chamou este método (não depende
        exclusivamente de `dashboard.py` nem de `orgs[0]`).

        Não concede nenhum privilégio especial a administradores internos
        (`is_internal_admin`) - esse flag não tem relação com vínculo de
        organização/produto; consultas administrativas que precisem ver
        todos os estados devem usar `list_organization_products_for_admin`
        (Issue #30) ou `OrganizationMember.query` diretamente, nunca este
        método.
        """
        active_membership = OrganizationService.get_active_membership(user_id, organization_id)
        if active_membership is None:
            raise ValueError("Usuário não possui vínculo ativo com esta organização.")

        # Pega todos os produtos base do sistema
        all_products = Product.query.all()

        # Pega os vínculos da organização atual
        org_prods = OrganizationProduct.query.filter_by(organization_id=organization_id).all()
        org_prods_dict = {op.product_id: op for op in org_prods}

        # Monta a estrutura de resposta para o Launcher
        launcher_items = []
        for p in all_products:
            org_prod = org_prods_dict.get(p.id)
            status = org_prod.status if org_prod else 'unsubscribed'

            launcher_items.append({
                'product': p,
                'status': status,
                'has_access': status in ['active', 'trial']
            })

        return launcher_items

    @staticmethod
    def list_organization_products_for_admin(organization_id):
        """Lista os produtos do catálogo estrutural e o estado de acesso da
        organização, para a tela administrativa (Issue #30).

        Diferente de `get_organization_products`, NÃO exige nenhum vínculo
        ativo do chamador - um administrador interno nunca tem vínculo com
        a organização cliente (Issue #19) e ainda assim precisa visualizar/
        operar esta tela. A autorização (ser administrador interno) é
        responsabilidade exclusiva da rota (`internal_admin_required`),
        nunca deste método.

        Consulta somente leitura: nunca cria nem altera nenhum registro.
        Usa 2 queries (produtos canônicos + vínculos da organização) e
        combina os dados em memória - nunca uma query por produto.

        Ordem determinística: sempre a ordem de `STRUCTURAL_PRODUCTS`
        (kalender, gedo, hunt) - nunca a ordem arbitrária do banco. Um
        produto estrutural que ainda não existe no banco (bootstrap nunca
        executado) é omitido da lista, nunca inventado.
        """
        if not Organization.query.filter_by(id=organization_id).first():
            raise ProductAccessError("Organização não encontrada.")

        products_by_code = {
            p.code: p for p in Product.query.filter(Product.code.in_(_CANONICAL_PRODUCT_CODES)).all()
        }

        org_products = OrganizationProduct.query.filter_by(organization_id=organization_id).all()
        org_products_by_product_id = {op.product_id: op for op in org_products}

        items = []
        for spec in STRUCTURAL_PRODUCTS:
            product = products_by_code.get(spec["code"])
            if product is None:
                continue

            org_product = org_products_by_product_id.get(product.id)
            status = org_product.status if org_product else 'unsubscribed'

            items.append({
                'product': product,
                'status': status,
                'has_access': status in ('active', 'trial'),
            })

        return items

    @staticmethod
    def _resolve_canonical_product(product_code):
        """Resolve um `Product` persistido a partir de um código do
        catálogo canônico (`STRUCTURAL_PRODUCTS`, Issue #27) - nunca aceita
        um `product_id` vindo do chamador/navegador.

        Rejeita dois cenários distintos, ambos com erro de domínio seguro
        (nunca uma exceção de banco/driver):
        - código fora do catálogo canônico, mesmo que exista algum
          `Product` com esse código no banco (proteção contra produto
          legado/manual criado fora do bootstrap oficial);
        - código canônico válido, mas sem `Product` correspondente ainda
          persistido (bootstrap estrutural nunca executado).
        """
        if product_code not in _CANONICAL_PRODUCT_CODES:
            raise ProductAccessError(f"Código de produto inválido: {product_code}")

        product = Product.query.filter_by(code=product_code).first()
        if not product:
            raise ProductAccessError(
                f"Produto estrutural '{product_code}' ainda não existe no banco. "
                "Execute o bootstrap estrutural antes de conceder ou revogar acesso."
            )
        return product

    @staticmethod
    def _apply_product_status(organization_id, product_code, actor_user_id, new_status, action):
        """Núcleo transacional compartilhado por `grant_product_access` e
        `revoke_product_access` (Issue #30).

        Validação (organização existe; `product_code` é canônico e já
        persistido) sempre roda antes de qualquer escrita. Mutação do
        `OrganizationProduct` e o `AuditLog` correspondente acontecem na
        MESMA transação (`AuditService.log_action(..., commit=False)`
        seguido de um único `db.session.commit()`) - qualquer falha reverte
        tudo via `db.session.rollback()`, nunca deixando alteração parcial
        nem um AuditLog órfão de uma mutação que não foi persistida.

        Idempotência: se o vínculo já está exatamente no `new_status`
        pedido, retorna sem tocar a linha (nenhum UPDATE, `updated_at`
        preservado) e sem criar AuditLog. Revogar um vínculo que nunca
        existiu também é no-op explícito, sem criar linha só para
        representar "sem acesso".

        Duas classes de erro, nunca confundidas: `ProductAccessError`
        (validação conhecida - sempre levantada diretamente, nunca
        capturada e reembalada aqui) propaga com sua mensagem já segura;
        qualquer outra exceção (banco, driver, AuditLog, bug) é convertida
        em `ProductAccessOperationError` com mensagem pública genérica,
        preservando a causa técnica original via `raise ... from exc` -
        nunca exibida ao usuário, mas disponível para quem logar a
        exceção (ver `app/blueprints/admin.py`).
        """
        try:
            if not Organization.query.filter_by(id=organization_id).first():
                raise ProductAccessError("Organização não encontrada.")

            product = AccessService._resolve_canonical_product(product_code)

            org_product = OrganizationProduct.query.filter_by(
                organization_id=organization_id,
                product_id=product.id,
            ).first()

            if org_product is None:
                if new_status == 'inactive':
                    return ProductAccessResult(organization_product=None, changed=False)

                old_status = None
                org_product = OrganizationProduct(
                    organization_id=organization_id,
                    product_id=product.id,
                    status=new_status,
                )
                db.session.add(org_product)
                # flush (não commit): obtém org_product.id, necessário para
                # o AuditLog abaixo, sem antecipar a persistência definitiva.
                db.session.flush()
            else:
                old_status = org_product.status
                if old_status == new_status:
                    return ProductAccessResult(organization_product=org_product, changed=False)
                org_product.status = new_status

            AuditService.log_action(
                user_id=actor_user_id,
                organization_id=organization_id,
                action=action,
                resource_type='organization_product',
                resource_id=org_product.id,
                details={
                    'product_code': product_code,
                    'old_status': old_status,
                    'new_status': new_status,
                },
                commit=False,
            )

            db.session.commit()
        except ProductAccessError:
            db.session.rollback()
            raise
        except Exception as exc:
            db.session.rollback()
            raise ProductAccessOperationError(
                "Não foi possível processar a operação de acesso a produto. "
                "Nenhuma alteração foi salva."
            ) from exc

        return ProductAccessResult(organization_product=org_product, changed=True)

    @staticmethod
    def grant_product_access(organization_id, product_code, actor_user_id):
        """Concede acesso ATIVO de uma ORGANIZAÇÃO a um produto do catálogo
        canônico (contrato/plano no nível da organização) - sempre define
        `status='active'` (Issue #30: 'trial' não é mais aceito por este
        método; quem precisar de 'trial' deve tratar como uma decisão de
        produto separada, fora do escopo desta Issue).

        Isto NÃO é uma operação de autorização de usuário - definir/alterar
        o status do contrato aqui não concede acesso a nenhuma pessoa
        especificamente. Quem decide se uma PESSOA pode usar o produto é
        sempre `get_organization_products(user_id, organization_id)`, que
        exige vínculo ativo além do contrato estar `active`/`trial`.

        `actor_user_id` é sempre recebido explicitamente (nunca lido de
        `current_user` dentro do service) - quem chama (a rota) é
        responsável por resolver o ator autenticado; mantém este método
        testável sem contexto de requisição e sem acoplamento oculto.
        """
        return AccessService._apply_product_status(
            organization_id,
            product_code,
            actor_user_id,
            new_status='active',
            action='organization.product.granted',
        )

    @staticmethod
    def revoke_product_access(organization_id, product_code, actor_user_id):
        """Revoga o acesso de uma ORGANIZAÇÃO a um produto do catálogo
        canônico - define `status='inactive'` (Issue #30: `'suspended'`
        não é usado por esta operação). Nunca apaga o `OrganizationProduct`
        - preserva a linha (e a `UniqueConstraint`) para que uma futura
        concessão reative exatamente o mesmo vínculo, com histórico
        completo no `AuditLog`.

        Revogar um vínculo que nunca existiu é um no-op seguro: não cria
        linha nenhuma, não gera AuditLog de uma mudança que não ocorreu.
        """
        return AccessService._apply_product_status(
            organization_id,
            product_code,
            actor_user_id,
            new_status='inactive',
            action='organization.product.revoked',
        )
