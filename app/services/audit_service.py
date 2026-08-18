from flask import request
from ..extensions import db
from ..models import AuditLog

class AuditService:
    @staticmethod
    def log_action(action, user_id=None, organization_id=None, resource_type=None, resource_id=None, details=None):
        # Capturar IP da request se estiver no contexto da request
        ip_address = None
        if request:
            ip_address = request.remote_addr
            
        log_entry = AuditLog(
            user_id=user_id,
            organization_id=organization_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
            ip_address=ip_address
        )
        db.session.add(log_entry)
        db.session.commit()
        return log_entry
