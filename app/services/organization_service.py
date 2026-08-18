import re
from flask import request
from flask_login import current_user
from ..extensions import db
from ..models import Organization, OrganizationMember, Role, User
from .audit_service import AuditService

class OrganizationService:
    @staticmethod
    def _clean_cnpj(cnpj):
        if not cnpj:
            return None
        cleaned = re.sub(r'\D', '', cnpj)
        return cleaned if cleaned else None

    @staticmethod
    def create_organization(legal_name, trade_name=None, cnpj=None, email=None, phone=None):
        cleaned_cnpj = OrganizationService._clean_cnpj(cnpj)
        
        org = Organization(
            legal_name=legal_name,
            trade_name=trade_name,
            cnpj=cleaned_cnpj,
            email=email,
            phone=phone
        )
        db.session.add(org)
        db.session.commit()
        
        # Log audit
        admin_id = current_user.id if current_user and current_user.is_authenticated else None
        AuditService.log_action(
            user_id=admin_id,
            action='organization.created',
            resource_type='organization',
            resource_id=str(org.id),
            details={'legal_name': legal_name, 'cnpj': cleaned_cnpj}
        )
        
        return org

    @staticmethod
    def add_member(organization_id, user_id, role_name):
        # Verifica se já é membro
        existing = OrganizationMember.query.filter_by(organization_id=organization_id, user_id=user_id).first()
        if existing:
            raise ValueError("O usuário já é membro desta organização.")

        role = Role.query.filter_by(name=role_name).first()
        if not role:
            # Em cenário de setup, criar caso não exista
            role = Role(name=role_name, description=f'Role {role_name}')
            db.session.add(role)
            db.session.flush()

        member = OrganizationMember(
            user_id=user_id,
            organization_id=organization_id,
            role_id=role.id
        )
        db.session.add(member)
        db.session.commit()

        admin_id = current_user.id if current_user and current_user.is_authenticated else None
        AuditService.log_action(
            user_id=admin_id,
            action='organization.member.added',
            resource_type='organization',
            resource_id=str(organization_id),
            details={'user_id': str(user_id), 'role': role_name}
        )
        
        return member

    @staticmethod
    def change_member_role(organization_id, user_id, new_role_name):
        member = OrganizationMember.query.filter_by(organization_id=organization_id, user_id=user_id).first()
        if not member:
            raise ValueError("O usuário não pertence a esta organização.")

        new_role = Role.query.filter_by(name=new_role_name).first()
        if not new_role:
            raise ValueError("O papel especificado não existe.")

        # Validação: Impedir remoção do último OWNER se o novo papel não for OWNER
        current_role = member.role
        if current_role and current_role.name == 'owner' and new_role_name != 'owner':
            owner_count = OrganizationMember.query.join(Role).filter(
                OrganizationMember.organization_id == organization_id,
                Role.name == 'owner'
            ).count()
            if owner_count <= 1:
                raise ValueError("A organização precisa possuir ao menos um proprietário (OWNER). Atribua outro proprietário antes de alterar o papel deste usuário.")

        member.role_id = new_role.id
        db.session.commit()

        admin_id = current_user.id if current_user and current_user.is_authenticated else None
        AuditService.log_action(
            user_id=admin_id,
            action='organization.member.role_changed',
            resource_type='organization',
            resource_id=str(organization_id),
            details={'user_id': str(user_id), 'new_role': new_role_name}
        )
        
        return member

    @staticmethod
    def remove_member(organization_id, user_id):
        member = OrganizationMember.query.filter_by(organization_id=organization_id, user_id=user_id).first()
        if not member:
            raise ValueError("O usuário não pertence a esta organização.")

        # Validação: Impedir remoção do último OWNER
        if member.role and member.role.name == 'owner':
            owner_count = OrganizationMember.query.join(Role).filter(
                OrganizationMember.organization_id == organization_id,
                Role.name == 'owner'
            ).count()
            if owner_count <= 1:
                raise ValueError("A organização precisa possuir ao menos um proprietário (OWNER). Atribua outro proprietário antes de remover este usuário.")

        db.session.delete(member)
        db.session.commit()

        admin_id = current_user.id if current_user and current_user.is_authenticated else None
        AuditService.log_action(
            user_id=admin_id,
            action='organization.member.removed',
            resource_type='organization',
            resource_id=str(organization_id),
            details={'removed_user_id': str(user_id)}
        )

    @staticmethod
    def get_user_organizations(user_id):
        memberships = OrganizationMember.query.filter_by(user_id=user_id).all()
        return [m.organization for m in memberships]
