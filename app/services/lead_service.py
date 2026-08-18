from sqlalchemy.exc import IntegrityError
from ..extensions import db
from ..models import Lead

class LeadService:
    @staticmethod
    def process_lead(idempotency_key, name, email, phone=None, company=None, source=None, metadata_data=None):
        """
        Processa a entrada de um Lead. Utiliza idempotency_key para evitar
        duplicação em casos de falha de rede/retries no webhook.
        """
        
        # Tentativa de inserção otimista baseada em unique constraint
        new_lead = Lead(
            idempotency_key=idempotency_key,
            name=name,
            email=email,
            phone=phone,
            company=company,
            source=source,
            metadata_data=metadata_data or {},
            status='new'
        )
        
        try:
            db.session.add(new_lead)
            db.session.commit()
            # TODO: Emitir evento para o sistema de notificações (celery/rabbitmq)
            return new_lead, True # True indica que foi criado
        except IntegrityError:
            db.session.rollback()
            # Se a constraint UNIQUE (idempotency_key) falhou, o lead já existe para essa chave
            existing_lead = Lead.query.filter_by(idempotency_key=idempotency_key).first()
            return existing_lead, False # False indica que já existia (idempotente)
