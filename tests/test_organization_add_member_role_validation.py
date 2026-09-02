from datetime import datetime

import pytest
from flask_sqlalchemy.query import Query as _FlaskSAQuery

from app.extensions import db
from app.models import AuditLog, Organization, OrganizationMember, Role, User
from app.services.organization_service import OrganizationError, OrganizationService

SYNTHETIC_PASSWORD = "senha-sintetica-issue-49-123"
INVALID_ROLE_MESSAGE = "Papel inválido: não pode ser vazio."


def _raise(*args, **kwargs):
    raise RuntimeError("synthetic-failure-issue-49")


def _create_user(email, *, is_internal_admin=False):
    user = User(
        name="Usuario Issue 49",
        email=email,
        is_internal_admin=is_internal_admin,
        email_verified_at=datetime.utcnow(),
    )
    user.set_password(SYNTHETIC_PASSWORD)
    db.session.add(user)
    db.session.commit()
    return user


def _create_organization(legal_name="Organizacao Issue 49"):
    org = Organization(legal_name=legal_name)
    db.session.add(org)
    db.session.commit()
    return org


def _login(client, get_csrf_token, email):
    return client.post("/login", data={
        "email": email,
        "password": SYNTHETIC_PASSWORD,
        "csrf_token": get_csrf_token(client),
    })


def _create_admin_and_login(client, app, get_csrf_token, email="admin.issue49@example.com"):
    with app.app_context():
        _create_user(email, is_internal_admin=True)
    _login(client, get_csrf_token, email)


# Serviço - rejeição de role_name inválido -------------------------------------

class TestAddMemberRejectsInvalidRoleName:
    @pytest.mark.parametrize(
        "invalid_role_name",
        [
            pytest.param(None, id="none"),
            pytest.param("", id="vazio"),
            pytest.param("   ", id="so_espacos"),
            pytest.param("\t\n  \t", id="whitespace_tab_e_quebra_de_linha"),
            pytest.param(123, id="nao_string_int"),
            pytest.param(["member"], id="nao_string_lista"),
        ],
    )
    def test_rejects_invalid_role_name_with_organization_error(self, app, invalid_role_name):
        with app.app_context():
            org = _create_organization()
            user = _create_user("usuario.papel.invalido@example.com")

            with pytest.raises(OrganizationError) as exc_info:
                OrganizationService.add_member(org.id, user.id, invalid_role_name)

            assert str(exc_info.value) == INVALID_ROLE_MESSAGE

    def test_rejection_creates_no_role(self, app):
        with app.app_context():
            org = _create_organization()
            user = _create_user("usuario.papel.sem.role.criada@example.com")
            roles_before = Role.query.count()

            with pytest.raises(OrganizationError):
                OrganizationService.add_member(org.id, user.id, "")

            assert Role.query.count() == roles_before

    def test_rejection_creates_no_organization_member(self, app):
        with app.app_context():
            org = _create_organization()
            user = _create_user("usuario.papel.sem.membro@example.com")

            with pytest.raises(OrganizationError):
                OrganizationService.add_member(org.id, user.id, "")

            assert OrganizationMember.query.filter_by(
                organization_id=org.id, user_id=user.id
            ).count() == 0

    def test_rejection_creates_no_audit_log(self, app):
        with app.app_context():
            org = _create_organization()
            user = _create_user("usuario.papel.sem.auditoria@example.com")
            audit_count_before = AuditLog.query.count()

            with pytest.raises(OrganizationError):
                OrganizationService.add_member(org.id, user.id, "   ")

            assert AuditLog.query.count() == audit_count_before

    def test_rejection_executes_zero_commits(self, app, monkeypatch):
        with app.app_context():
            org = _create_organization()
            user = _create_user("usuario.papel.zero.commits@example.com")

            commit_calls = []
            original_commit = db.session.commit

            def _counting_commit():
                commit_calls.append(1)
                return original_commit()

            with monkeypatch.context() as m:
                m.setattr(db.session, "commit", _counting_commit)
                with pytest.raises(OrganizationError):
                    OrganizationService.add_member(org.id, user.id, None)

            assert len(commit_calls) == 0

    def test_rejection_never_reaches_any_query(self, app, monkeypatch):
        """Prova comportamental (sem inspecionar código-fonte) de que a
        validação roda ANTES de qualquer consulta relacionada ao papel (ou a
        qualquer outra coisa): se qualquer `Query.filter_by` fosse alcançado,
        a exceção sintética faria `add_member` reembalar como
        `OrganizationOperationError` (branch `except Exception`), nunca
        `OrganizationError` diretamente. Obter exatamente `OrganizationError`
        com a mensagem esperada prova que nenhuma consulta rodou antes da
        rejeição."""
        with app.app_context():
            org = _create_organization()
            user = _create_user("usuario.papel.sem.consulta@example.com")

            with monkeypatch.context() as m:
                m.setattr(_FlaskSAQuery, "filter_by", _raise)
                with pytest.raises(OrganizationError) as exc_info:
                    OrganizationService.add_member(org.id, user.id, "")

            assert str(exc_info.value) == INVALID_ROLE_MESSAGE

    def test_session_usable_after_rejection(self, app):
        with app.app_context():
            org = _create_organization()
            failing_user = _create_user("usuario.papel.falha.sessao@example.com")
            ok_user = _create_user("usuario.papel.ok.sessao@example.com")

            with pytest.raises(OrganizationError):
                OrganizationService.add_member(org.id, failing_user.id, "")

            member = OrganizationService.add_member(org.id, ok_user.id, "member")
            assert member is not None
            assert OrganizationMember.query.filter_by(
                organization_id=org.id, user_id=ok_user.id
            ).count() == 1


# Serviço - preservação do fluxo válido ----------------------------------------

class TestAddMemberValidRoleNameStillWorks:
    def test_reuses_existing_role(self, app):
        with app.app_context():
            org = _create_organization()
            user = _create_user("usuario.papel.reutilizado@example.com")
            existing_role = Role(name="owner-issue-49", description="Role owner-issue-49")
            db.session.add(existing_role)
            db.session.commit()
            existing_role_id = existing_role.id
            roles_before = Role.query.count()

            member = OrganizationService.add_member(org.id, user.id, "owner-issue-49")

            assert member.role_id == existing_role_id
            assert Role.query.count() == roles_before

    def test_creates_new_role_when_valid_name_does_not_exist(self, app):
        with app.app_context():
            org = _create_organization()
            user = _create_user("usuario.papel.novo@example.com")
            roles_before = Role.query.count()

            member = OrganizationService.add_member(org.id, user.id, "papel-novo-issue-49")

            assert Role.query.count() == roles_before + 1
            new_role = Role.query.filter_by(name="papel-novo-issue-49").first()
            assert new_role is not None
            assert member.role_id == new_role.id

    def test_valid_flow_creates_member_and_audit_log(self, app):
        with app.app_context():
            org = _create_organization()
            user = _create_user("usuario.papel.membro.auditoria@example.com")
            audit_count_before = AuditLog.query.count()

            member = OrganizationService.add_member(org.id, user.id, "member")

            assert OrganizationMember.query.filter_by(
                organization_id=org.id, user_id=user.id
            ).count() == 1
            assert AuditLog.query.filter_by(action="organization.member.added").count() == \
                audit_count_before + 1

    def test_valid_role_name_with_surrounding_whitespace_is_preserved_unmodified(self, app):
        with app.app_context():
            org = _create_organization()
            user = _create_user("usuario.papel.espacos@example.com")
            padded_name = "  member-issue-49  "

            member = OrganizationService.add_member(org.id, user.id, padded_name)

            persisted_role = Role.query.get(member.role_id)
            assert persisted_role.name == padded_name
            # Nenhuma normalização: uma Role com o nome já sem espaços não é
            # a mesma linha, nem é criada/reaproveitada por esta chamada.
            assert Role.query.filter_by(name="member-issue-49").first() is None


# Rota HTTP: POST /admin/organizations/<organization_id>/members --------------

class TestAddMemberRouteRejectsInvalidRole:
    @pytest.mark.parametrize(
        "form_data_overrides",
        [
            pytest.param({}, id="campo_role_ausente"),
            pytest.param({"role": ""}, id="role_vazio"),
            pytest.param({"role": "   "}, id="role_so_espacos"),
        ],
    )
    def test_invalid_role_shows_safe_message_and_creates_nothing(
        self, client, app, get_csrf_token, caplog, form_data_overrides
    ):
        with app.app_context():
            org = _create_organization()
            org_id = org.id
            target_user = _create_user("usuario.rota.papel.invalido@example.com")
            target_user_id = target_user.id
        _create_admin_and_login(client, app, get_csrf_token)

        form_data = {
            "user_id": str(target_user_id),
            "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
        }
        form_data.update(form_data_overrides)

        with caplog.at_level("ERROR"):
            response = client.post(
                f"/admin/organizations/{org_id}/members",
                data=form_data,
                follow_redirects=True,
            )

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert INVALID_ROLE_MESSAGE in html
        assert not any("Falha inesperada" in r.message for r in caplog.records)

        with app.app_context():
            assert OrganizationMember.query.filter_by(
                organization_id=org_id, user_id=target_user_id
            ).count() == 0

    def test_valid_role_still_works_after_change(self, client, app, get_csrf_token):
        with app.app_context():
            org = _create_organization()
            org_id = org.id
            target_user = _create_user("usuario.rota.papel.valido@example.com")
            target_user_id = target_user.id
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.post(
            f"/admin/organizations/{org_id}/members",
            data={
                "user_id": str(target_user_id),
                "role": "member",
                "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
            },
        )

        assert response.status_code == 302
        with app.app_context():
            assert OrganizationMember.query.filter_by(
                organization_id=org_id, user_id=target_user_id
            ).count() == 1

    def test_unauthenticated_request_is_redirected_without_mutation(self, client, app):
        with app.app_context():
            org_id = _create_organization().id

        response = client.post(f"/admin/organizations/{org_id}/members", data={"role": ""})

        assert response.status_code in (302, 400)
        with app.app_context():
            assert OrganizationMember.query.filter_by(organization_id=org_id).count() == 0

    def test_missing_csrf_token_returns_400(self, client, app, get_csrf_token):
        with app.app_context():
            org = _create_organization()
            org_id = org.id
            target_user = _create_user("usuario.rota.sem.csrf@example.com")
            target_user_id = target_user.id
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.post(f"/admin/organizations/{org_id}/members", data={
            "user_id": str(target_user_id),
            "role": "",
        })

        assert response.status_code == 400
        with app.app_context():
            assert OrganizationMember.query.filter_by(
                organization_id=org_id, user_id=target_user_id
            ).count() == 0
