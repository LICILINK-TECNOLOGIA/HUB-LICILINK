import os
from dotenv import load_dotenv
load_dotenv()

from app import create_app
from app.extensions import db
from app.models import Product, OrganizationProduct
from app.services.auth_service import AuthService
from app.services.access_service import AccessService

def seed_db():
    app = create_app()
    with app.app_context():
        # Limpar produtos caso já existam para não duplicar (script amigável para rodar de novo)
        if Product.query.count() == 0:
            print("Criando Produtos SaaS...")
            p1 = Product(name='L-Kalender', code='kalender', description='Gestão de prazos e eventos de licitação.', url='https://kalender-hml.licilink.com.br')
            p2 = Product(name='L-GeDo', code='gedo', description='Gestão Eletrônica de Documentos para concorrências.', url='https://gedo-hml.licilink.com.br')
            p3 = Product(name='L-Hunt', code='hunt', description='Inteligência e monitoramento de editais de compras públicas.', url='https://hunt-hml.licilink.com.br')
            db.session.add_all([p1, p2, p3])
            db.session.commit()
            print("Produtos criados com sucesso.")
        
        # Criar Usuários e Organizações
        try:
            print("Criando Usuário Admin (Tudo liberado)...")
            admin_user, admin_org = AuthService.register_user_and_organization(
                name="Administrador",
                email="admin@licilink.com",
                password="admin",
                org_name="LiciLink Plataforma"
            )
            # Dá acesso ativo a tudo para a LiciLink Plataforma
            AccessService.grant_product_access(admin_org.id, 'kalender', 'active')
            AccessService.grant_product_access(admin_org.id, 'gedo', 'active')
            AccessService.grant_product_access(admin_org.id, 'hunt', 'active')
            
            print("Criando Usuário Básico (Alguns acessos)...")
            joao_user, joao_org = AuthService.register_user_and_organization(
                name="João da Silva",
                email="joao@empresa.com",
                password="senha",
                org_name="Comércio Alpha Ltda"
            )
            # João tem Kalender ativo e L-Hunt em trial
            AccessService.grant_product_access(joao_org.id, 'kalender', 'active')
            AccessService.grant_product_access(joao_org.id, 'hunt', 'trial')
            
            print("Criando Usuário Sem Assinatura...")
            maria_user, maria_org = AuthService.register_user_and_organization(
                name="Maria Souza",
                email="maria@construtora.com",
                password="senha",
                org_name="Construtora Beta"
            )
            # Maria não recebe acessos, verá tudo como "Não Assinado" e botão "Conhecer"
            
            print("Seed finalizado com sucesso!")
        except Exception as e:
            print(f"Erro ao popular dados (pode ser que já existam): {str(e)}")

if __name__ == '__main__':
    seed_db()
