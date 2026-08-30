from ..extensions import db
from ..models import OrganizationProduct, Product
from .organization_service import OrganizationService

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
        todos os estados devem usar `OrganizationMember.query` diretamente
        (ex.: em `app/blueprints/admin.py`), nunca este método.
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
    def grant_product_access(organization_id, product_code, status='active'):
        """Concede ou altera o acesso de uma ORGANIZAÇÃO a um produto
        (contrato/plano no nível da organização).

        Isto NÃO é uma operação de autorização de usuário - não recebe nem
        verifica `user_id` de propósito, e definir/alterar o status do
        contrato aqui não concede acesso a nenhuma pessoa especificamente.
        Quem decide se uma PESSOA pode usar o produto é sempre
        `get_organization_products(user_id, organization_id)`, que exige
        vínculo ativo além do contrato estar `active`/`trial`. Use este
        método apenas para operações administrativas de plano/contrato,
        nunca como substituto do portão de autorização por usuário.
        """
        product = Product.query.filter_by(code=product_code).first()
        if not product:
            raise ValueError(f"Produto não encontrado: {product_code}")

        org_prod = OrganizationProduct.query.filter_by(
            organization_id=organization_id,
            product_id=product.id
        ).first()

        if org_prod:
            org_prod.status = status
        else:
            org_prod = OrganizationProduct(
                organization_id=organization_id,
                product_id=product.id,
                status=status
            )
            db.session.add(org_prod)

        db.session.commit()
        return org_prod
