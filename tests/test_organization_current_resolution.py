"""Issue #71 (revisão técnica, achado A2): `OrganizationService.get_user_organizations`
não tinha `.order_by()` - a ordem devolvida não era garantida pelo SQL/
SQLAlchemy, e `dashboard.index` usava `orgs[0]` sobre esse resultado sem
ordenação. Estes testes cobrem: (1) a ordenação explícita e
determinística agora aplicada (`OrganizationMember.created_at` asc.,
`id` asc. como desempate); (2) o novo helper único
`OrganizationService.resolve_current_organization`, usado tanto pelo GET
quanto pelo POST do launcher (ver `tests/test_launcher_product_action.py`
e `tests/test_launcher_launch_route.py` para os testes de consistência
GET/POST em nível de HTTP).

Todos os testes fixam `created_at` explicitamente quando a ordem importa
- nunca dependem de `time.sleep` nem da resolução do relógio real entre
duas chamadas. A query usada (`ORDER BY` sobre colunas de valor,
comparação de timestamp/UUID) é padrão SQL, sem nenhuma extensão
específica de dialeto - o mesmo comportamento é esperado tanto no
PostgreSQL de produção quanto no SQLite efêmero usado por estes testes."""
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import Organization, OrganizationMember, User
from app.services.organization_service import OrganizationService

SYNTHETIC_PASSWORD = "senha-sintetica-issue-71-resolucao"


def _create_user(email, *, name="Usuario Issue 71"):
    user = User(name=name, email=email, email_verified_at=datetime.utcnow())
    user.set_password(SYNTHETIC_PASSWORD)
    db.session.add(user)
    db.session.commit()
    return user


def _create_organization(legal_name):
    org = Organization(legal_name=legal_name)
    db.session.add(org)
    db.session.commit()
    return org


def _set_membership_created_at(organization_id, user_id, created_at):
    membership = OrganizationMember.query.filter_by(
        organization_id=organization_id, user_id=user_id,
    ).one()
    membership.created_at = created_at
    db.session.commit()


class TestGetUserOrganizationsDeterministicOrdering:
    def test_orders_by_membership_created_at_ascending(self, app):
        with app.app_context():
            user = _create_user("ordenacao.created_at@example.com")
            org_a = _create_organization("Organizacao A - Vinculo Mais Novo")
            org_b = _create_organization("Organizacao B - Vinculo Mais Antigo")
            org_c = _create_organization("Organizacao C - Vinculo Intermediario")

            OrganizationService.add_member(org_a.id, user.id, "member")
            OrganizationService.add_member(org_b.id, user.id, "member")
            OrganizationService.add_member(org_c.id, user.id, "member")

            base = datetime(2026, 1, 1, tzinfo=timezone.utc)
            _set_membership_created_at(org_a.id, user.id, base + timedelta(days=2))
            _set_membership_created_at(org_b.id, user.id, base)
            _set_membership_created_at(org_c.id, user.id, base + timedelta(days=1))

            orgs = OrganizationService.get_user_organizations(user.id)

            assert [o.id for o in orgs] == [org_b.id, org_c.id, org_a.id]

    def test_insertion_order_different_from_created_at_order_is_ignored(self, app):
        """A ordem em que as linhas são inseridas no banco (e, portanto,
        a ordem física/PK provável) é deliberadamente DIVERGENTE da
        ordem esperada por `created_at` - comprova que a ordenação segue
        o valor da coluna, nunca a ordem de inserção."""
        with app.app_context():
            user = _create_user("ordenacao.insercao_divergente@example.com")
            org_recent = _create_organization("Organizacao Inserida Primeiro, Vinculo Mais Novo")
            org_earliest = _create_organization("Organizacao Inserida Depois, Vinculo Mais Antigo")

            # Inserção física: org_recent primeiro, org_earliest depois.
            OrganizationService.add_member(org_recent.id, user.id, "member")
            OrganizationService.add_member(org_earliest.id, user.id, "member")

            base = datetime(2026, 1, 1, tzinfo=timezone.utc)
            _set_membership_created_at(org_recent.id, user.id, base + timedelta(days=1))
            _set_membership_created_at(org_earliest.id, user.id, base)

            orgs = OrganizationService.get_user_organizations(user.id)

            assert orgs[0].id == org_earliest.id
            assert orgs[1].id == org_recent.id

    def test_tiebreaks_identical_created_at_by_membership_id_deterministically(self, app):
        """Duas memberships com `created_at` IDÊNTICO - a ordenação deve
        ser estável (mesma ordem em chamadas repetidas), decidida pelo
        critério de desempate (`OrganizationMember.id`), nunca por uma
        ordem que varie entre chamadas."""
        with app.app_context():
            user = _create_user("ordenacao.empate@example.com")
            org_x = _create_organization("Organizacao Empate X")
            org_y = _create_organization("Organizacao Empate Y")

            OrganizationService.add_member(org_x.id, user.id, "member")
            OrganizationService.add_member(org_y.id, user.id, "member")

            same_instant = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
            _set_membership_created_at(org_x.id, user.id, same_instant)
            _set_membership_created_at(org_y.id, user.id, same_instant)

            member_x = OrganizationMember.query.filter_by(organization_id=org_x.id, user_id=user.id).one()
            member_y = OrganizationMember.query.filter_by(organization_id=org_y.id, user_id=user.id).one()
            expected_first_org_id = org_x.id if member_x.id < member_y.id else org_y.id

            first_call = OrganizationService.get_user_organizations(user.id)
            second_call = OrganizationService.get_user_organizations(user.id)

            assert first_call[0].id == expected_first_org_id
            assert second_call[0].id == expected_first_org_id
            assert [o.id for o in first_call] == [o.id for o in second_call]

    def test_suspended_membership_is_never_selected(self, app):
        with app.app_context():
            user = _create_user("ordenacao.suspenso@example.com")
            org = _create_organization("Organizacao Vinculo Suspenso")
            OrganizationService.add_member(org.id, user.id, "member")
            OrganizationService.suspend_member(org.id, user.id)

            orgs = OrganizationService.get_user_organizations(user.id)

            assert orgs == []

    def test_existing_contract_return_type_and_empty_list_preserved(self, app):
        """`get_user_organizations` continua devolvendo uma lista de
        `Organization` (nunca IDs, nunca `OrganizationMember`), vazia
        quando não há vínculo elegível - mesmo contrato já usado por todo
        o restante do projeto antes desta Issue."""
        with app.app_context():
            user = _create_user("ordenacao.contrato@example.com")

            assert OrganizationService.get_user_organizations(user.id) == []

            org = _create_organization("Organizacao Contrato Preservado")
            OrganizationService.add_member(org.id, user.id, "member")

            orgs = OrganizationService.get_user_organizations(user.id)
            assert len(orgs) == 1
            assert isinstance(orgs[0], Organization)
            assert orgs[0].id == org.id


class TestResolveCurrentOrganization:
    def test_returns_the_organization_from_the_earliest_active_membership(self, app):
        with app.app_context():
            user = _create_user("resolucao.primeira@example.com")
            org_recent = _create_organization("Organizacao Resolve Recente")
            org_earliest = _create_organization("Organizacao Resolve Antiga")

            OrganizationService.add_member(org_recent.id, user.id, "member")
            OrganizationService.add_member(org_earliest.id, user.id, "member")

            base = datetime(2026, 1, 1, tzinfo=timezone.utc)
            _set_membership_created_at(org_recent.id, user.id, base + timedelta(days=1))
            _set_membership_created_at(org_earliest.id, user.id, base)

            resolved = OrganizationService.resolve_current_organization(user.id)

            assert resolved is not None
            assert resolved.id == org_earliest.id

    def test_returns_none_when_user_has_no_eligible_membership(self, app):
        with app.app_context():
            user = _create_user("resolucao.nenhuma@example.com")

            assert OrganizationService.resolve_current_organization(user.id) is None

    def test_returns_none_when_only_membership_is_suspended(self, app):
        with app.app_context():
            user = _create_user("resolucao.suspenso@example.com")
            org = _create_organization("Organizacao Resolve Suspensa")
            OrganizationService.add_member(org.id, user.id, "member")
            OrganizationService.suspend_member(org.id, user.id)

            assert OrganizationService.resolve_current_organization(user.id) is None

    def test_repeated_calls_over_same_state_return_the_same_organization(self, app):
        """Base direta do requisito de consistência GET/POST: duas
        chamadas sucessivas, sem nenhuma mudança de estado entre elas,
        devem sempre devolver a mesma organização - condição necessária
        para que o GET do launcher e o POST de lançamento nunca
        divirjam (cobertura em nível de HTTP em
        `tests/test_launcher_launch_route.py::TestOrganizationConsistencyBetweenGetAndPost`)."""
        with app.app_context():
            user = _create_user("resolucao.repeticao@example.com")
            org_a = _create_organization("Organizacao Repeticao A")
            org_b = _create_organization("Organizacao Repeticao B")
            OrganizationService.add_member(org_a.id, user.id, "member")
            OrganizationService.add_member(org_b.id, user.id, "member")

            first = OrganizationService.resolve_current_organization(user.id)
            second = OrganizationService.resolve_current_organization(user.id)

            assert first.id == second.id

    def test_single_organization_user_behavior_is_unchanged(self, app):
        """Usuário com exatamente uma organização - comportamento
        idêntico ao existente antes desta correção (mesma organização
        sempre resolvida)."""
        with app.app_context():
            user = _create_user("resolucao.unica@example.com")
            org = _create_organization("Organizacao Unica Issue 71")
            OrganizationService.add_member(org.id, user.id, "member")

            resolved = OrganizationService.resolve_current_organization(user.id)

            assert resolved is not None
            assert resolved.id == org.id

    def test_does_not_duplicate_the_underlying_query(self, app, monkeypatch):
        """`resolve_current_organization` delega inteiramente para
        `get_user_organizations` - nenhuma consulta própria adicional."""
        with app.app_context():
            user = _create_user("resolucao.semduplicacao@example.com")
            org = _create_organization("Organizacao Sem Duplicacao Issue 71")
            OrganizationService.add_member(org.id, user.id, "member")

            calls = []
            original = OrganizationService.get_user_organizations

            def _spy(user_id):
                calls.append(user_id)
                return original(user_id)

            monkeypatch.setattr(OrganizationService, "get_user_organizations", staticmethod(_spy))

            OrganizationService.resolve_current_organization(user.id)

            assert calls == [user.id]
