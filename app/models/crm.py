from sqlalchemy.dialects.postgresql import JSONB
from .base import BaseModel
from ..extensions import db

class Lead(BaseModel):
    __tablename__ = 'leads'

    # Controle de Idempotência
    idempotency_key = db.Column(db.String(255), unique=True, nullable=False, index=True)

    # Dados do Lead
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(50))
    company = db.Column(db.String(255))
    
    # Origem e Metadados (JSON para flexibilidade de UTMs)
    source = db.Column(db.String(100)) # Ex: 'site_contato', 'l_kalender_trial'
    metadata_data = db.Column(JSONB, default=dict) # utm_source, utm_medium, etc.
    
    # Status
    status = db.Column(db.String(50), default='new', nullable=False) # 'new', 'contacted', 'converted', 'lost'
