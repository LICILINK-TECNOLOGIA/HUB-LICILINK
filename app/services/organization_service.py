import re
from flask import request
from flask_login import current_user
from ..extensions import db
from ..models import Organization, OrganizationMember, Role, User
from ..models.identity import OrganizationMemberStatus
from .audit_service import AuditService


class OrganizationError(ValueError):
    """Erro de domínio esperado e seguro (vínculo duplicado, papel
    inexistente, último owner ativo, administrador interno rejeitado,
    etc.) - a mensagem já é curada para ser exibida diretamente ao
    operador, nunca contém detalhe de banco/driver. Continua sendo um
    `ValueError` (compatibilidade com `pytest.raises(ValueError)` já usado
    pelos chamadores existentes), mas nunca é a mesma classe usada para uma
    falha inesperada - ver `OrganizationOperationError`."""


class OrganizationOperationError(ValueError):
    """Falha inesperada ao processar a operação (banco, driver, AuditLog,
    ou qualquer exceção não prevista) - deliberadamente NÃO é subclasse de
    `OrganizationError` (são classes irmãs), para que uma rota consiga
    capturar uma sem capturar a outra. A mensagem pública desta exceção é
    sempre genérica; a causa técnica real é preservada em `__cause__` via
    `raise ... from exc`, nunca exposta ao usuário - só para quem
    inspecionar/logar a exceção no servidor (ver `app/blueprints/admin.py`).
    Mesmo padrão já estabelecido por `ProductAccessError`/
    `ProductAccessOperationError` em `app/services/access_service.py`."""


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
        """Cria a organização e o AuditLog correspondente em UMA ÚNICA
        transação/commit (Issue #41) - antes, eram dois commits separados
        (organização, depois AuditLog); se o segundo falhasse, a
        organização já estava persistida mas a rota exibia erro ao
        operador (persistência parcial silenciosa). Qualquer falha agora
        reverte tudo via `db.session.rollback()`, nunca deixando uma
        organização órfã de AuditLog nem um AuditLog de uma organização
        que não foi persistida."""
        cleaned_cnpj = OrganizationService._clean_cnpj(cnpj)

        try:
            org = Organization(
                legal_name=legal_name,
                trade_name=trade_name,
                cnpj=cleaned_cnpj,
                email=email,
                phone=phone
            )
            db.session.add(org)
            # flush (não commit): obtém org.id, necessário para o AuditLog
            # abaixo, sem antecipar a persistência definitiva.
            db.session.flush()

            admin_id = current_user.id if current_user and current_user.is_authenticated else None
            AuditService.log_action(
                user_id=admin_id,
                action='organization.created',
                resource_type='organization',
                resource_id=org.id,
                details={'legal_name': legal_name, 'cnpj': cleaned_cnpj},
                commit=False,
            )

            db.session.commit()
        except OrganizationError:
            db.session.rollback()
            raise
        except Exception as exc:
            db.session.rollback()
            raise OrganizationOperationError(
                "Não foi possível criar a organização. Nenhuma alteração foi salva."
            ) from exc

        return org

    @staticmethod
    def _user_is_internal_admin(user_id):
        """Predicado central único (Issue #19): verdadeiro se o usuário
        existir e for administrador interno (`User.is_internal_admin=True`).

        Esta é a ÚNICA função que lê `User.is_internal_admin` para decidir
        elegibilidade de vínculo organizacional - todo validador (que
        levanta erro) e toda consulta (que filtra/oculta) usam este mesmo
        predicado, nunca reimplementam a checagem, para a decisão nunca
        divergir entre os pontos que a aplicam."""
        user = User.query.filter_by(id=user_id).first()
        return user is not None and user.is_internal_admin

    @staticmethod
    def _reject_internal_admin_as_member(user_id):
        """Política central (Issue #19, decisão de produto já tomada):
        um usuário com `User.is_internal_admin=True` nunca pode ter um
        vínculo ATIVO como `OrganizationMember`, nem ter esse vínculo
        administrado como se fosse legítimo. Usado por:
        - `add_member` - nunca cria um novo vínculo;
        - `_apply_status_transition`, quando `new_status='active'` - nunca
          reativa (`reactivate_member`) nem restaura
          (`restore_removed_member`) um vínculo legado suspenso/removido;
        - `change_member_role` - nunca promove/rebaixa/reorganiza o papel
          de um vínculo (mesmo legado), pois isso administraria como
          legítimo um vínculo que nunca deveria existir.

        A administração interna permanece inteiramente separada de
        Role/OrganizationMember; ela usa suas próprias rotas
        (`internal_admin_required`), nunca este mecanismo -
        `is_internal_admin` nunca é bypass nem concede/nega acesso
        organizacional por si só, é apenas um impeditivo de vínculo.

        Levanta `ValueError` ANTES de qualquer lock, mutação,
        `Role`/`OrganizationMember`/`AuditLog` ser criado/alterado e antes
        de qualquer `db.session.commit()` - deve ser chamado como a
        primeira validação em cada um dos pontos de entrada acima. Nunca
        modifica um vínculo já existente por si só - apenas impede que a
        operação chamadora prossiga. Suspender/remover um vínculo legado
        (saneamento) NÃO passa por aqui - continua sempre permitido."""
        if OrganizationService._user_is_internal_admin(user_id):
            raise OrganizationError(
                "Administradores internos não podem ser vinculados como membros de organizações clientes."
            )

    @staticmethod
    def add_member(organization_id, user_id, role_name):
        """Cria o vínculo e o AuditLog correspondente em UMA ÚNICA
        transação/commit (Issue #41) - mesmo motivo/mesma correção de
        `create_organization`: dois commits separados deixavam o vínculo
        persistido mesmo quando só o AuditLog falhava. Assinatura e
        retorno (`OrganizationMember`) preservados - usado como helper de
        setup por dezenas de outros testes."""
        try:
            OrganizationService._reject_internal_admin_as_member(user_id)

            # Verifica se já existe um vínculo, em qualquer status. Nunca cria
            # uma segunda linha (violaria a constraint de unicidade) nem reativa
            # silenciosamente um vínculo removido/suspenso - isso deve passar
            # por uma operação administrativa explícita e separada
            # (restore_removed_member / reactivate_member).
            existing = OrganizationMember.query.filter_by(organization_id=organization_id, user_id=user_id).first()
            if existing:
                if existing.status == OrganizationMemberStatus.REMOVED.value:
                    raise OrganizationError(
                        "Este usuário já teve um vínculo removido com esta organização. "
                        "Use uma restauração administrativa explícita (restore_removed_member) "
                        "para reativá-lo, em vez de criar um novo vínculo."
                    )
                raise OrganizationError("O usuário já é membro desta organização.")

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
            # flush (não commit): obtém member.id, necessário para o
            # AuditLog abaixo, sem antecipar a persistência definitiva.
            db.session.flush()

            admin_id = current_user.id if current_user and current_user.is_authenticated else None
            AuditService.log_action(
                user_id=admin_id,
                action='organization.member.added',
                resource_type='organization',
                resource_id=organization_id,
                details={'user_id': str(user_id), 'role': role_name, 'status': member.status},
                commit=False,
            )

            db.session.commit()
        except OrganizationError:
            db.session.rollback()
            raise
        except Exception as exc:
            db.session.rollback()
            raise OrganizationOperationError(
                "Não foi possível atualizar o vínculo da organização. Nenhuma alteração foi salva."
            ) from exc

        return member

    @staticmethod
    def change_member_role(organization_id, user_id, new_role_name):
        try:
            # Issue #19: um vínculo (mesmo legado) de administrador interno
            # nunca pode ter o papel administrado/reorganizado - isso o
            # trataria como um vínculo legítimo. Checado ANTES do lock e de
            # qualquer leitura/mutação de OrganizationMember.
            OrganizationService._reject_internal_admin_as_member(user_id)

            # Bloqueia a linha da Organization ANTES de contar owners ativos
            # ou alterar qualquer papel - mesma invariante, mesmo lock e
            # mesma ordem de `_apply_status_transition` (ver
            # `_lock_organization_row`), evitando TOCTOU e deadlock entre
            # operações concorrentes na mesma organização.
            OrganizationService._lock_organization_row(organization_id)

            member = OrganizationMember.query.filter_by(organization_id=organization_id, user_id=user_id).first()
            if not member:
                raise OrganizationError("O usuário não pertence a esta organização.")

            new_role = Role.query.filter_by(name=new_role_name).first()
            if not new_role:
                raise OrganizationError("O papel especificado não existe.")

            # Validação: Impedir remoção do último OWNER ATIVO se o novo papel não for OWNER
            current_role = member.role
            if current_role and current_role.name == 'owner' and new_role_name != 'owner':
                owner_count = OrganizationMember.query.join(Role).filter(
                    OrganizationMember.organization_id == organization_id,
                    OrganizationMember.status == OrganizationMemberStatus.ACTIVE.value,
                    Role.name == 'owner'
                ).count()
                if owner_count <= 1:
                    raise OrganizationError("A organização precisa possuir ao menos um proprietário (OWNER) ativo. Atribua outro proprietário antes de alterar o papel deste usuário.")

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
        except OrganizationError:
            db.session.rollback()
            raise
        except Exception as exc:
            db.session.rollback()
            raise OrganizationOperationError(
                "Erro ao alterar o papel do membro. Nenhuma alteração foi salva."
            ) from exc

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
        detido além do necessário).

        Issue #19: se a transição leva o vínculo para 'active' (caso de
        `reactivate_member`, suspended->active, e `restore_removed_member`,
        removed->active), a política central
        `_reject_internal_admin_as_member` é aplicada ANTES de adquirir o
        lock ou tocar qualquer dado - um administrador interno nunca pode
        ter um vínculo legado (suspenso/removido) transformado em ativo por
        aqui. Transições para 'suspended'/'removed' NÃO passam por essa
        checagem - suspender/remover um vínculo legado inválido continua
        sempre possível, para permitir saneamento sem deixar o vínculo
        preso em um estado impossível de corrigir."""
        try:
            if new_status == OrganizationMemberStatus.ACTIVE.value:
                OrganizationService._reject_internal_admin_as_member(user_id)

            OrganizationService._lock_organization_row(organization_id)

            if new_status != OrganizationMemberStatus.ACTIVE.value:
                if member.role and member.role.name == 'owner':
                    owner_count = OrganizationMember.query.join(Role).filter(
                        OrganizationMember.organization_id == organization_id,
                        OrganizationMember.status == OrganizationMemberStatus.ACTIVE.value,
                        Role.name == 'owner'
                    ).count()
                    if owner_count <= 1:
                        raise OrganizationError(
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
        except OrganizationError:
            db.session.rollback()
            raise
        except Exception as exc:
            db.session.rollback()
            raise OrganizationOperationError(
                "Erro ao alterar o status do vínculo. Nenhuma alteração foi salva."
            ) from exc

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
            raise OrganizationError(
                f"Status inválido: deve ser um destes valores: {', '.join(sorted(valid_statuses))}."
            )

        member = OrganizationMember.query.filter_by(organization_id=organization_id, user_id=user_id).first()
        if not member:
            raise OrganizationError("O usuário não pertence a esta organização.")

        if new_status == member.status:
            raise OrganizationError("O vínculo já está neste status.")

        allowed_targets = OrganizationService._ALLOWED_STATUS_TRANSITIONS.get(member.status, set())
        if new_status not in allowed_targets:
            raise OrganizationError(
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
            raise OrganizationError("O usuário não pertence a esta organização.")

        if member.status != OrganizationMemberStatus.REMOVED.value:
            raise OrganizationError("Esta operação só é permitida para vínculos removidos.")

        return OrganizationService._apply_status_transition(
            organization_id, user_id, member,
            OrganizationMemberStatus.ACTIVE.value,
            'organization.member.restored',
        )

    @staticmethod
    def get_user_organizations(user_id):
        """Retorna somente as organizações às quais o usuário possui vínculo
        ATIVO. Vínculos suspensos ou removidos nunca aparecem aqui.

        Issue #19: retorna lista vazia para administrador interno
        (`User.is_internal_admin=True`), mesmo que exista um vínculo ativo
        legado no banco - sem alterar a linha. Usa o mesmo predicado central
        (`_user_is_internal_admin`) de `get_active_membership`/
        `_reject_internal_admin_as_member`, para a decisão nunca divergir
        entre os pontos que a aplicam. Esta função é usada por
        `dashboard.index()` para escolher a organização "atual" do usuário
        - retornar vazio aqui impede que uma organização cliente legada
        chegue a ser selecionada como `current_org` para um administrador
        interno, e portanto impede que `AccessService.get_organization_products`
        chegue a ser chamado (e levante `ValueError`) nesse fluxo."""
        if OrganizationService._user_is_internal_admin(user_id):
            return []

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
        seleção de organização ativa e para o futuro handoff de SSO. Esse
        contrato (OrganizationMember ou None, nunca exceção) é preservado
        sem alteração - quem precisa tratar "sem vínculo" como erro (ex.:
        `AccessService.get_organization_products`) já faz essa conversão
        no próprio chamador.

        Issue #19: também retorna None se o usuário do vínculo for um
        administrador interno (`User.is_internal_admin=True`, mesmo
        predicado central `_user_is_internal_admin` usado por
        `_reject_internal_admin_as_member`/`get_user_organizations`), mesmo
        que a linha em si esteja com status 'active' - cobre o vínculo
        legado que possa ter sido criado antes desta política existir, sem
        precisar alterar a linha. Este é o único portão de autorização
        usado por `AccessService.get_organization_products`, então a mesma
        checagem central protege automaticamente o acesso a produtos, sem
        exigir nenhuma mudança em AccessService."""
        membership = OrganizationMember.query.filter_by(
            user_id=user_id,
            organization_id=organization_id,
            status=OrganizationMemberStatus.ACTIVE.value,
        ).first()
        if membership is not None and OrganizationService._user_is_internal_admin(user_id):
            return None
        return membership
