from ..extensions import db
from ..models import Organization, OrganizationMember, Role

class OrganizationService:
    @staticmethod
    def create_organization(name, owner_user_id, cnpj=None):
        org = Organization(name=name, cnpj=cnpj)
        db.session.add(org)
        db.session.flush() # Para pegar o ID da org gerado
        
        owner_role = Role.query.filter_by(name='owner').first()
        if not owner_role:
            # Em um cenário real de setup, essas roles já deveriam existir
            owner_role = Role(name='owner', description='Dono da Organização')
            db.session.add(owner_role)
            db.session.flush()
            
        member = OrganizationMember(
            user_id=owner_user_id,
            organization_id=org.id,
            role_id=owner_role.id
        )
        db.session.add(member)
        db.session.commit()
        
        return org

    @staticmethod
    def get_user_organizations(user_id):
        memberships = OrganizationMember.query.filter_by(user_id=user_id).all()
        return [m.organization for m in memberships]
