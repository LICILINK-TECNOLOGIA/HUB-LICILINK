from flask import request
from ..extensions import db
from ..models import AuditLog

class AuditService:
    @staticmethod
    def log_action(action, user_id=None, organization_id=None, resource_type=None, resource_id=None, details=None, commit=True):
        """Registra uma entrada de auditoria.

        `commit=True` (padrão, preserva o comportamento de todos os
        chamadores existentes) grava imediatamente. `commit=False` apenas
        adiciona a entrada à sessão sem commitar - uso previsto para quando
        o chamador precisa que a entrada de auditoria faça parte da MESMA
        transação/commit de outra alteração (ex.: mudança de status de
        vínculo em `OrganizationService`), garantindo atomicidade: se o
        commit final falhar, nem a alteração nem a auditoria são
        persistidas.
        """
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
        if commit:
            db.session.commit()
        return log_entry
