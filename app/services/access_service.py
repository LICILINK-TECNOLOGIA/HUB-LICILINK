from ..extensions import db
from ..models import OrganizationProduct, Product

class AccessService:
    @staticmethod
    def get_organization_products(organization_id):
        """
        Retorna todos os produtos disponíveis no sistema e o status deles 
        para a organização especificada. Usado para montar o Launcher.
        """
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
        """Concede ou altera o acesso de uma organização a um produto"""
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
