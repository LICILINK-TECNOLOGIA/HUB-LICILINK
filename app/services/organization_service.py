import re
from flask import request
from flask_login import current_user
from ..extensions import db
from ..models import Organization, OrganizationMember, Role, User
from ..models.identity import OrganizationMemberStatus
from .audit_service import AuditService

class OrganizationService:
    @staticmethod
    def _lock_organization_row(organization_id):
        """Bloqueia (`SELECT ... FOR UPDATE`) a linha de `Organization`
        correspondente, para serializar qualquer operação concorrente que
        dependa da invariante "a organização sempre tem ao menos um OWNER
        ativo".

        Qual linha é bloqueada: sempre a linha de `Organization`
        (`organization_id`), nunca a linha de `OrganizationMember` em si -
        é o recurso compartilhado que todas as operações concorrentes de
        membros da MESMA organização disputam.

        Quando é adquirido: no início de `change_member_role` e de
        `_apply_status_transition` (usado por suspend_member,
        remove_member, reactivate_member e restore_removed_member) - antes
        de contar owners ativos e antes de qualquer mutação.

        Quando é liberado: automaticamente ao final da transação desta
        mesma chamada (COMMIT em caso de sucesso, ROLLBACK explícito em
        qualquer falha - nunca liberado manualmente no meio, e nunca detido
        além do necessário).

        Por que impede duas operações concorrentes de zerarem os owners
        ativos: no PostgreSQL, `with_for_update()` emite
        `SELECT ... FOR UPDATE`, que bloqueia de verdade a segunda
        transação concorrente na MESMA linha de Organization até a primeira
        commitar ou reverter. Se duas requisições tentarem suspender/
        remover/trocar o papel do(s) último(s) owner(s) ativo(s) ao mesmo
        tempo, a segunda só prossegue DEPOIS que a primeira já
        commitou (ou reverteu) - nesse momento ela conta os owners ativos
        já refletindo o resultado da primeira operação, nunca um valor
        obsoleto (elimina o TOCTOU). Todas as operações protegidas
        adquirem o MESMO lock (linha de Organization) e na MESMA ordem
        (primeiro o lock, só depois qualquer leitura/mutação de
        OrganizationMember), o que evita deadlock entre elas.

        No SQLite (usado nos testes automatizados), `with_for_update()` não
        bloqueia de fato - SQLite não implementa lock de linha no mesmo
        sentido do PostgreSQL. Por isso os testes desta suíte verificam que
        o método É CHAMADO (via mock/spy) antes da contagem/alteração, não
        o comportamento de bloqueio real sob concorrência, que exigiria
        PostgreSQL de verdade (ver nota de homologação nos testes).
        """
        return Organization.query.filter_by(id=organization_id).with_for_update().first()

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
            resource_id=org.id,
            details={'legal_name': legal_name, 'cnpj': cleaned_cnpj}
        )

        return org

    @staticmethod
    def add_member(organization_id, user_id, role_name):
        # Verifica se já existe um vínculo, em qualquer status. Nunca cria
        # uma segunda linha (violaria a constraint de unicidade) nem reativa
        # silenciosamente um vínculo removido/suspenso - isso deve passar
        # por uma operação administrativa explícita e separada
        # (restore_removed_member / reactivate_member).
        existing = OrganizationMember.query.filter_by(organization_id=organization_id, user_id=user_id).first()
        if existing:
            if existing.status == OrganizationMemberStatus.REMOVED.value:
                raise ValueError(
                    "Este usuário já teve um vínculo removido com esta organização. "
                    "Use uma restauração administrativa explícita (restore_removed_member) "
                    "para reativá-lo, em vez de criar um novo vínculo."
                )
            raise ValueError("O usuário já é membro desta organização.")

        role = Role.query.filter_by(name=role_name).first()
        if not role:
            # Em cenário de setup, criar caso não exista
            role = Role(name=role_name, description=f'Role {role_name}')
            db.session.add(role)
            db.session.flush()

        # Novo vínculo criado por ação administrativa explícita (não há fluxo
        # de convite/aceite pendente no projeto) - default seguro e
        # documentado: 'active' (ver OrganizationMemberStatus).
        member = OrganizationMember(
            user_id=user_id,
            organization_id=organization_id,
            role_id=role.id,
            status=OrganizationMemberStatus.ACTIVE.value,
        )
        db.session.add(member)
        db.session.commit()

        admin_id = current_user.id if current_user and current_user.is_authenticated else None
        AuditService.log_action(
            user_id=admin_id,
            action='organization.member.added',
            resource_type='organization',
            resource_id=organization_id,
            details={'user_id': str(user_id), 'role': role_name, 'status': member.status}
        )

        return member

    @staticmethod
    def change_member_role(organization_id, user_id, new_role_name):
        try:
            # Bloqueia a linha da Organization ANTES de contar owners ativos
            # ou alterar qualquer papel - mesma invariante, mesmo lock e
            # mesma ordem de `_apply_status_transition` (ver
            # `_lock_organization_row`), evitando TOCTOU e deadlock entre
            # operações concorrentes na mesma organização.
            OrganizationService._lock_organization_row(organization_id)

            member = OrganizationMember.query.filter_by(organization_id=organization_id, user_id=user_id).first()
            if not member:
                raise ValueError("O usuário não pertence a esta organização.")

            new_role = Role.query.filter_by(name=new_role_name).first()
            if not new_role:
                raise ValueError("O papel especificado não existe.")

            # Validação: Impedir remoção do último OWNER ATIVO se o novo papel não for OWNER
            current_role = member.role
            if current_role and current_role.name == 'owner' and new_role_name != 'owner':
                owner_count = OrganizationMember.query.join(Role).filter(
                    OrganizationMember.organization_id == organization_id,
                    OrganizationMember.status == OrganizationMemberStatus.ACTIVE.value,
                    Role.name == 'owner'
                ).count()
                if owner_count <= 1:
                    raise ValueError("A organização precisa possuir ao menos um proprietário (OWNER) ativo. Atribua outro proprietário antes de alterar o papel deste usuário.")

            member.role_id = new_role.id

            admin_id = current_user.id if current_user and current_user.is_authenticated else None
            AuditService.log_action(
                user_id=admin_id,
                action='organization.member.role_changed',
                resource_type='organization',
                resource_id=organization_id,
                details={'user_id': str(user_id), 'new_role': new_role_name},
                commit=False,
            )

            db.session.commit()
        except ValueError:
            db.session.rollback()
            raise
        except Exception:
            db.session.rollback()
            raise ValueError("Erro ao alterar o papel do membro. Nenhuma alteração foi salva.")

        return member

    # Máquina de estados: transições permitidas a partir de cada status
    # atual, através do ponto de entrada genérico `change_member_status`.
    # 'removed' não permite nenhuma transição genérica (nem para 'suspended'
    # nem de volta para 'active') - é tratado como estado terminal na via
    # normal, só recuperável por uma operação administrativa explícita e
    # separada (`restore_removed_member`), nunca silenciosamente via
    # `reactivate_member` ou `change_member_status`.
    _ALLOWED_STATUS_TRANSITIONS = {
        OrganizationMemberStatus.ACTIVE.value: {
            OrganizationMemberStatus.SUSPENDED.value,
            OrganizationMemberStatus.REMOVED.value,
        },
        OrganizationMemberStatus.SUSPENDED.value: {
            OrganizationMemberStatus.ACTIVE.value,
            OrganizationMemberStatus.REMOVED.value,
        },
        OrganizationMemberStatus.REMOVED.value: set(),
    }

    @staticmethod
    def _apply_status_transition(organization_id, user_id, member, new_status, action):
        """Executa a transição já validada: bloqueia a linha da Organization
        (`_lock_organization_row`) antes de contar owners ativos, protege o
        último OWNER ativo, e persiste a mudança de status junto com a
        entrada de AuditLog em UMA ÚNICA transação/commit.

        Atomicidade: se qualquer etapa falhar (proteção de owner, escrita
        do AuditLog ou o commit final), TUDO é revertido via
        `db.session.rollback()` - nunca há commit intermediário, e o
        rollback também libera o lock da Organization imediatamente (nunca
        detido além do necessário)."""
        try:
            OrganizationService._lock_organization_row(organization_id)

            if new_status != OrganizationMemberStatus.ACTIVE.value:
                if member.role and member.role.name == 'owner':
                    owner_count = OrganizationMember.query.join(Role).filter(
                        OrganizationMember.organization_id == organization_id,
                        OrganizationMember.status == OrganizationMemberStatus.ACTIVE.value,
                        Role.name == 'owner'
                    ).count()
                    if owner_count <= 1:
                        raise ValueError(
                            "A organização precisa possuir ao menos um proprietário (OWNER) ativo. "
                            "Atribua outro proprietário antes de alterar o status deste usuário."
                        )

            old_status = member.status
            member.status = new_status

            admin_id = current_user.id if current_user and current_user.is_authenticated else None
            AuditService.log_action(
                user_id=admin_id,
                action=action,
                resource_type='organization_member',
                resource_id=member.id,
                details={
                    'organization_id': str(organization_id),
                    'target_user_id': str(user_id),
                    'old_status': old_status,
                    'new_status': new_status,
                },
                commit=False,
            )

            db.session.commit()
        except ValueError:
            db.session.rollback()
            raise
        except Exception:
            db.session.rollback()
            raise ValueError("Erro ao alterar o status do vínculo. Nenhuma alteração foi salva.")

        return member

    @staticmethod
    def change_member_status(organization_id, user_id, new_status, action='organization.member.status_changed'):
        """Transição transacional e validada de status do vínculo.

        Rejeita qualquer valor que não seja um dos estados controlados em
        `OrganizationMemberStatus`, qualquer transição não permitida pela
        máquina de estados (`_ALLOWED_STATUS_TRANSITIONS`) e qualquer
        transição para o mesmo estado atual (rejeitada explicitamente, não
        tratada como operação idempotente) - tudo isso antes de qualquer
        alteração no banco. É o único ponto de entrada público para mudar o
        status de um vínculo já existente e ativo/suspenso; 'removed' só é
        revertido por `restore_removed_member`, nunca por aqui."""
        valid_statuses = {status.value for status in OrganizationMemberStatus}
        if new_status not in valid_statuses:
            raise ValueError(
                f"Status inválido: deve ser um destes valores: {', '.join(sorted(valid_statuses))}."
            )

        member = OrganizationMember.query.filter_by(organization_id=organization_id, user_id=user_id).first()
        if not member:
            raise ValueError("O usuário não pertence a esta organização.")

        if new_status == member.status:
            raise ValueError("O vínculo já está neste status.")

        allowed_targets = OrganizationService._ALLOWED_STATUS_TRANSITIONS.get(member.status, set())
        if new_status not in allowed_targets:
            raise ValueError(
                f"Transição de status inválida: '{member.status}' -> '{new_status}'."
            )

        return OrganizationService._apply_status_transition(
            organization_id, user_id, member, new_status, action
        )

    @staticmethod
    def suspend_member(organization_id, user_id):
        """Suspende o vínculo (nunca apaga a linha - preserva histórico)."""
        return OrganizationService.change_member_status(
            organization_id, user_id,
            OrganizationMemberStatus.SUSPENDED.value,
            'organization.member.suspended',
        )

    @staticmethod
    def reactivate_member(organization_id, user_id):
        """Reativa um vínculo SUSPENSO (apenas suspended -> active).

        Para restaurar um vínculo REMOVIDO, use `restore_removed_member` -
        operação deliberadamente separada e com nome explícito, pois
        'removed' representa desligamento e não deve ser revertido
        silenciosamente pelo mesmo caminho usado para uma simples
        suspensão temporária."""
        return OrganizationService.change_member_status(
            organization_id, user_id,
            OrganizationMemberStatus.ACTIVE.value,
            'organization.member.reactivated',
        )

    @staticmethod
    def remove_member(organization_id, user_id):
        """Desliga o usuário da organização.

        Não apaga a linha - marca o vínculo como 'removed', preservando
        histórico e permitindo auditoria/restauração administrativa futura,
        se necessário.
        """
        return OrganizationService.change_member_status(
            organization_id, user_id,
            OrganizationMemberStatus.REMOVED.value,
            'organization.member.removed',
        )

    @staticmethod
    def restore_removed_member(organization_id, user_id):
        """Restaura um vínculo REMOVIDO para 'active'.

        Operação administrativa explícita e separada de `reactivate_member`
        (que trata apenas suspended -> active) - 'removed' representa
        desligamento e só deve ser revertido por uma decisão deliberada e
        auditável, nunca reutilizando silenciosamente outra operação.
        """
        member = OrganizationMember.query.filter_by(organization_id=organization_id, user_id=user_id).first()
        if not member:
            raise ValueError("O usuário não pertence a esta organização.")

        if member.status != OrganizationMemberStatus.REMOVED.value:
            raise ValueError("Esta operação só é permitida para vínculos removidos.")

        return OrganizationService._apply_status_transition(
            organization_id, user_id, member,
            OrganizationMemberStatus.ACTIVE.value,
            'organization.member.restored',
        )

    @staticmethod
    def get_user_organizations(user_id):
        """Retorna somente as organizações às quais o usuário possui vínculo
        ATIVO. Vínculos suspensos ou removidos nunca aparecem aqui."""
        memberships = OrganizationMember.query.filter_by(
            user_id=user_id,
            status=OrganizationMemberStatus.ACTIVE.value,
        ).all()
        return [m.organization for m in memberships]

    @staticmethod
    def get_active_membership(user_id, organization_id):
        """Revalida, consultando o banco (nunca um valor de sessão em cache),
        se o usuário possui vínculo ativo com esta organização especfica.
        Retorna o OrganizationMember se ativo, ou None caso contrário
        (inexistente, suspenso ou removido) - uso previsto para qualquer
        seleção de organização ativa e para o futuro handoff de SSO."""
        return OrganizationMember.query.filter_by(
            user_id=user_id,
            organization_id=organization_id,
            status=OrganizationMemberStatus.ACTIVE.value,
        ).first()
