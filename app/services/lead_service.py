from sqlalchemy.exc import IntegrityError
from ..extensions import db
from ..models import Lead

LEAD_OPERATION_ERROR_MESSAGE = "Não foi possível processar o lead. Tente novamente."


class LeadOperationError(ValueError):
    """Falha inesperada ao processar o lead (Issue #47) - mensagem
    pública sempre fixa (`LEAD_OPERATION_ERROR_MESSAGE`), causa técnica
    real preservada via `raise ... from exc`, nunca exposta ao chamador.
    Nunca usada para payload inválido (isso é responsabilidade da rota,
    antes de chegar aqui) - só para falha genuinamente inesperada de
    persistência: um `IntegrityError` sem lead correspondente já
    existente (violação de integridade não relacionada à idempotência
    de `idempotency_key`), qualquer outra exceção em add/commit, ou
    falha na própria consulta de reconciliação pós-rollback."""


class LeadService:
    @staticmethod
    def process_lead(idempotency_key, name, email, phone=None, company=None, source=None, metadata_data=None):
        """
        Processa a entrada de um Lead. Utiliza idempotency_key para evitar
        duplicação em casos de falha de rede/retries no webhook.

        Retorna sempre `(lead, created)` com `lead` sendo uma instância
        real de `Lead` - nunca `(None, False)` (Issue #47). Um
        `IntegrityError` só é tratado como idempotência genuína quando a
        consulta subsequente por `idempotency_key` de fato encontra um
        registro; qualquer outro caso (violação de integridade não
        relacionada, falha na própria consulta, ou qualquer exceção não
        prevista em add/commit) vira `LeadOperationError`, nunca é
        silenciosamente interpretado como duplicidade. Nenhuma mensagem
        de constraint/driver/SQL é inspecionada em nenhum momento.
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
            return new_lead, True  # True indica que foi criado
        except IntegrityError as exc:
            db.session.rollback()
            # Se a constraint UNIQUE (idempotency_key) falhou, o lead já
            # existe para essa chave - mas um IntegrityError também pode
            # vir de qualquer outra violação de integridade (NOT NULL,
            # CHECK, FK). Só é tratado como idempotência genuína se a
            # consulta realmente encontrar o registro; caso contrário é
            # uma falha operacional real, nunca (None, False).
            try:
                existing_lead = Lead.query.filter_by(idempotency_key=idempotency_key).first()
            except Exception as query_exc:
                db.session.rollback()
                raise LeadOperationError(LEAD_OPERATION_ERROR_MESSAGE) from query_exc

            if existing_lead is not None:
                return existing_lead, False  # False indica que já existia (idempotente)

            raise LeadOperationError(LEAD_OPERATION_ERROR_MESSAGE) from exc
        except Exception as exc:
            db.session.rollback()
            raise LeadOperationError(LEAD_OPERATION_ERROR_MESSAGE) from exc
