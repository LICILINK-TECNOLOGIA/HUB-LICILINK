import uuid
from datetime import datetime

import pytest

from app.extensions import db
from app.models import AuditLog, Organization, OrganizationMember, User
from app.services import audit_service as audit_service_module
from app.services.organization_service import OrganizationService

SYNTHETIC_PASSWORD = "senha-sintetica-issue-41-http-123"


def _create_user(email, *, is_internal_admin=False):
    user = User(
        name="Usuario Issue 41 HTTP",
        email=email,
        is_internal_admin=is_internal_admin,
        email_verified_at=datetime.utcnow(),
    )
    user.set_password(SYNTHETIC_PASSWORD)
    db.session.add(user)
    db.session.commit()
    return user


def _login(client, get_csrf_token, email):
    return client.post("/login", data={
        "email": email,
        "password": SYNTHETIC_PASSWORD,
        "csrf_token": get_csrf_token(client),
    })


def _create_organization(legal_name="Organizacao Issue 41 HTTP", cnpj=None):
    org = Organization(legal_name=legal_name, cnpj=cnpj)
    db.session.add(org)
    db.session.commit()
    return org


def _create_admin_and_login(client, app, get_csrf_token, email="admin.http.issue41@example.com"):
    with app.app_context():
        _create_user(email, is_internal_admin=True)
    _login(client, get_csrf_token, email)


def _raise_commit():
    raise RuntimeError("synthetic failure - constraint uq_sintetica_issue41 violated")


def _raise_audit(*args, **kwargs):
    raise RuntimeError("synthetic audit failure - issue 41")


# create_organization --------------------------------------------------------

class TestCreateOrganizationErrorHandling:
    def test_unexpected_commit_failure_shows_generic_message_and_logs_exception(
        self, client, app, get_csrf_token, monkeypatch, caplog
    ):
        _create_admin_and_login(client, app, get_csrf_token)

        with monkeypatch.context() as m:
            m.setattr(db.session, "commit", _raise_commit)
            with caplog.at_level("ERROR"):
                response = client.post("/admin/organizations", data={
                    "legal_name": "Org Falha Sintetica Issue 41",
                    "csrf_token": get_csrf_token(client, "/admin/organizations/new"),
                }, follow_redirects=True)

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "Não foi possível criar a organização. Nenhuma alteração foi salva." in html
        assert "synthetic failure" not in html
        assert "constraint" not in html.lower()
        assert "uq_sintetica_issue41" not in html
        assert "Traceback" not in html

        unexpected_logs = [r for r in caplog.records if "Falha inesperada" in r.message]
        assert len(unexpected_logs) == 1

        with app.app_context():
            assert Organization.query.filter_by(legal_name="Org Falha Sintetica Issue 41").first() is None
            assert AuditLog.query.filter_by(action="organization.created").count() == 0

    def test_audit_log_failure_does_not_persist_organization(self, client, app, get_csrf_token, monkeypatch):
        _create_admin_and_login(client, app, get_csrf_token)

        with monkeypatch.context() as m:
            m.setattr(audit_service_module.AuditService, "log_action", _raise_audit)
            response = client.post("/admin/organizations", data={
                "legal_name": "Org Auditoria Falha Issue 41",
                "csrf_token": get_csrf_token(client, "/admin/organizations/new"),
            }, follow_redirects=True)

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "Não foi possível criar a organização. Nenhuma alteração foi salva." in html
        with app.app_context():
            assert Organization.query.filter_by(legal_name="Org Auditoria Falha Issue 41").first() is None

    def test_real_duplicate_cnpj_conflict_shows_generic_message_not_driver_text(
        self, client, app, get_csrf_token
    ):
        # Diferente dos testes acima (falha sintética via monkeypatch), este
        # provoca um IntegrityError DE VERDADE (violação real da unique
        # constraint de `cnpj`), sem nenhum mock - prova que o wrapping da
        # Issue #41 também protege contra uma exceção genuína do
        # SQLAlchemy/driver, não só contra uma simulada.
        with app.app_context():
            _create_organization("Org Original CNPJ Issue 41", cnpj="11222333000181")
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.post("/admin/organizations", data={
            "legal_name": "Org Duplicada CNPJ Issue 41",
            "cnpj": "11.222.333/0001-81",
            "csrf_token": get_csrf_token(client, "/admin/organizations/new"),
        }, follow_redirects=True)

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "Não foi possível criar a organização. Nenhuma alteração foi salva." in html
        # Não depende do nome específico da constraint gerada pelo SQLite
        # (pode variar por dialeto/versão) - só confirma ausência de
        # qualquer vocabulário de driver/SQL na resposta.
        assert "UNIQUE constraint" not in html
        assert "IntegrityError" not in html
        assert "Traceback" not in html
        with app.app_context():
            assert Organization.query.filter_by(legal_name="Org Duplicada CNPJ Issue 41").first() is None

        # Sessão continua utilizável após o rollback do conflito real -
        # uma criação subsequente, sem conflito, deve funcionar normalmente.
        response = client.post("/admin/organizations", data={
            "legal_name": "Org Depois Do Conflito CNPJ Issue 41",
            "csrf_token": get_csrf_token(client, "/admin/organizations/new"),
        })
        assert response.status_code == 302
        with app.app_context():
            assert Organization.query.filter_by(
                legal_name="Org Depois Do Conflito CNPJ Issue 41"
            ).first() is not None

    def test_session_usable_after_rollback(self, client, app, get_csrf_token, monkeypatch):
        _create_admin_and_login(client, app, get_csrf_token)

        with monkeypatch.context() as m:
            m.setattr(db.session, "commit", _raise_commit)
            client.post("/admin/organizations", data={
                "legal_name": "Org Falha Antes Issue 41",
                "csrf_token": get_csrf_token(client, "/admin/organizations/new"),
            })

        response = client.post("/admin/organizations", data={
            "legal_name": "Org Depois Da Falha Issue 41",
            "csrf_token": get_csrf_token(client, "/admin/organizations/new"),
        })

        assert response.status_code == 302
        with app.app_context():
            assert Organization.query.filter_by(legal_name="Org Depois Da Falha Issue 41").first() is not None


# add_member ------------------------------------------------------------------

class TestAddMemberErrorHandling:
    def test_domain_error_duplicate_member_shows_safe_message_without_unexpected_log(
        self, client, app, get_csrf_token, caplog
    ):
        with app.app_context():
            org = _create_organization()
            org_id = org.id
            member_user = _create_user("membro.duplicado.issue41@example.com")
            OrganizationService.add_member(org_id, member_user.id, "member")
            member_user_id = member_user.id
        _create_admin_and_login(client, app, get_csrf_token)

        with caplog.at_level("ERROR"):
            response = client.post(f"/admin/organizations/{org_id}/members", data={
                "user_id": str(member_user_id),
                "role": "member",
                "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
            }, follow_redirects=True)

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        # Texto legado preservado exatamente (migração ValueError -> OrganizationError).
        assert "O usuário já é membro desta organização." in html
        assert not any("Falha inesperada" in r.message for r in caplog.records)
        with app.app_context():
            assert OrganizationMember.query.filter_by(
                organization_id=org_id, user_id=member_user_id
            ).count() == 1

    def test_unexpected_commit_failure_shows_generic_message_and_logs_exception(
        self, client, app, get_csrf_token, monkeypatch, caplog
    ):
        with app.app_context():
            org = _create_organization()
            org_id = org.id
            new_user = _create_user("membro.novo.issue41@example.com")
            new_user_id = new_user.id
        _create_admin_and_login(client, app, get_csrf_token)

        with monkeypatch.context() as m:
            m.setattr(db.session, "commit", _raise_commit)
            with caplog.at_level("ERROR"):
                response = client.post(f"/admin/organizations/{org_id}/members", data={
                    "user_id": str(new_user_id),
                    "role": "member",
                    "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
                }, follow_redirects=True)

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "Não foi possível atualizar o vínculo da organização. Nenhuma alteração foi salva." in html
        assert "synthetic failure" not in html
        assert "constraint" not in html.lower()
        assert "Traceback" not in html

        unexpected_logs = [r for r in caplog.records if "Falha inesperada" in r.message]
        assert len(unexpected_logs) == 1

        with app.app_context():
            assert OrganizationMember.query.filter_by(
                organization_id=org_id, user_id=new_user_id
            ).count() == 0
            assert AuditLog.query.filter_by(action="organization.member.added").count() == 0

    def test_audit_log_failure_does_not_persist_membership(self, client, app, get_csrf_token, monkeypatch):
        with app.app_context():
            org = _create_organization()
            org_id = org.id
            new_user = _create_user("membro.auditoria.issue41@example.com")
            new_user_id = new_user.id
        _create_admin_and_login(client, app, get_csrf_token)

        with monkeypatch.context() as m:
            m.setattr(audit_service_module.AuditService, "log_action", _raise_audit)
            response = client.post(f"/admin/organizations/{org_id}/members", data={
                "user_id": str(new_user_id),
                "role": "member",
                "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
            }, follow_redirects=True)

        assert response.status_code == 200
        with app.app_context():
            assert OrganizationMember.query.filter_by(
                organization_id=org_id, user_id=new_user_id
            ).count() == 0

    def test_session_usable_after_rollback(self, client, app, get_csrf_token, monkeypatch):
        with app.app_context():
            org = _create_organization()
            org_id = org.id
            failing_user = _create_user("membro.falha.issue41@example.com")
            failing_user_id = failing_user.id
            ok_user = _create_user("membro.ok.issue41@example.com")
            ok_user_id = ok_user.id
        _create_admin_and_login(client, app, get_csrf_token)

        with monkeypatch.context() as m:
            m.setattr(db.session, "commit", _raise_commit)
            client.post(f"/admin/organizations/{org_id}/members", data={
                "user_id": str(failing_user_id),
                "role": "member",
                "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
            })

        response = client.post(f"/admin/organizations/{org_id}/members", data={
            "user_id": str(ok_user_id),
            "role": "member",
            "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
        })

        assert response.status_code == 302
        with app.app_context():
            assert OrganizationMember.query.filter_by(
                organization_id=org_id, user_id=failing_user_id
            ).count() == 0
            assert OrganizationMember.query.filter_by(
                organization_id=org_id, user_id=ok_user_id
            ).count() == 1


# change_member_role -----------------------------------------------------------

class TestChangeMemberRoleErrorHandling:
    def test_domain_error_nonexistent_role_shows_safe_message_without_unexpected_log(
        self, client, app, get_csrf_token, caplog
    ):
        with app.app_context():
            org = _create_organization()
            org_id = org.id
            member_user = _create_user("membro.papel.issue41@example.com")
            OrganizationService.add_member(org_id, member_user.id, "member")
            member_user_id = member_user.id
        _create_admin_and_login(client, app, get_csrf_token)

        with caplog.at_level("ERROR"):
            response = client.post(
                f"/admin/organizations/{org_id}/members/{member_user_id}/role",
                data={
                    "role": "papel-inexistente-issue-41",
                    "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
                },
                follow_redirects=True,
            )

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "O papel especificado não existe." in html
        assert not any("Falha inesperada" in r.message for r in caplog.records)

    def test_unexpected_commit_failure_shows_generic_message_and_logs_exception(
        self, client, app, get_csrf_token, monkeypatch, caplog
    ):
        with app.app_context():
            org = _create_organization()
            org_id = org.id
            member_user = _create_user("membro.papel.falha.issue41@example.com")
            OrganizationService.add_member(org_id, member_user.id, "member")
            member_user_id = member_user.id
        _create_admin_and_login(client, app, get_csrf_token)

        with monkeypatch.context() as m:
            m.setattr(db.session, "commit", _raise_commit)
            with caplog.at_level("ERROR"):
                response = client.post(
                    f"/admin/organizations/{org_id}/members/{member_user_id}/role",
                    data={
                        "role": "member",
                        "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
                    },
                    follow_redirects=True,
                )

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "Erro ao alterar o papel do membro. Nenhuma alteração foi salva." in html
        assert "synthetic failure" not in html
        assert "Traceback" not in html

        unexpected_logs = [r for r in caplog.records if "Falha inesperada" in r.message]
        assert len(unexpected_logs) == 1


# remove_member -----------------------------------------------------------------

class TestRemoveMemberErrorHandling:
    def test_domain_error_last_active_owner_shows_safe_message_without_unexpected_log(
        self, client, app, get_csrf_token, caplog
    ):
        with app.app_context():
            org = _create_organization()
            org_id = org.id
            owner_user = _create_user("owner.unico.issue41@example.com")
            OrganizationService.add_member(org_id, owner_user.id, "owner")
            owner_user_id = owner_user.id
        _create_admin_and_login(client, app, get_csrf_token)

        with caplog.at_level("ERROR"):
            response = client.post(
                f"/admin/organizations/{org_id}/members/{owner_user_id}/remove",
                data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
                follow_redirects=True,
            )

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "precisa possuir ao menos um proprietário" in html
        assert not any("Falha inesperada" in r.message for r in caplog.records)
        with app.app_context():
            member = OrganizationMember.query.filter_by(
                organization_id=org_id, user_id=owner_user_id
            ).first()
            assert member.status == "active"

    def test_unexpected_commit_failure_shows_generic_message_and_logs_exception(
        self, client, app, get_csrf_token, monkeypatch, caplog
    ):
        with app.app_context():
            org = _create_organization()
            org_id = org.id
            member_user = _create_user("membro.remocao.falha.issue41@example.com")
            OrganizationService.add_member(org_id, member_user.id, "member")
            member_user_id = member_user.id
        _create_admin_and_login(client, app, get_csrf_token)

        with monkeypatch.context() as m:
            m.setattr(db.session, "commit", _raise_commit)
            with caplog.at_level("ERROR"):
                response = client.post(
                    f"/admin/organizations/{org_id}/members/{member_user_id}/remove",
                    data={"csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")},
                    follow_redirects=True,
                )

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "Não foi possível remover o membro. Tente novamente." in html or \
               "Erro ao alterar o status do vínculo. Nenhuma alteração foi salva." in html
        assert "synthetic failure" not in html
        assert "Traceback" not in html

        unexpected_logs = [r for r in caplog.records if "Falha inesperada" in r.message]
        assert len(unexpected_logs) == 1

        with app.app_context():
            member = OrganizationMember.query.filter_by(
                organization_id=org_id, user_id=member_user_id
            ).first()
            assert member.status == "active"


# link_user_to_org --------------------------------------------------------------

class TestLinkUserToOrgErrorHandling:
    def test_domain_error_duplicate_member_shows_safe_message_without_unexpected_log(
        self, client, app, get_csrf_token, caplog
    ):
        with app.app_context():
            org = _create_organization()
            org_id = org.id
            member_user = _create_user("membro.link.duplicado.issue41@example.com")
            OrganizationService.add_member(org_id, member_user.id, "member")
            member_user_id = member_user.id
        _create_admin_and_login(client, app, get_csrf_token)

        with caplog.at_level("ERROR"):
            response = client.post(f"/admin/users/{member_user_id}/organization", data={
                "organization_id": str(org_id),
                "role": "member",
                "csrf_token": get_csrf_token(client, "/admin/users-orgs"),
            }, follow_redirects=True)

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "O usuário já é membro desta organização." in html
        assert not any("Falha inesperada" in r.message for r in caplog.records)

    def test_unexpected_commit_failure_shows_generic_message_and_logs_exception(
        self, client, app, get_csrf_token, monkeypatch, caplog
    ):
        with app.app_context():
            org = _create_organization()
            org_id = org.id
            new_user = _create_user("usuario.link.falha.issue41@example.com")
            new_user_id = new_user.id
        _create_admin_and_login(client, app, get_csrf_token)

        with monkeypatch.context() as m:
            m.setattr(db.session, "commit", _raise_commit)
            with caplog.at_level("ERROR"):
                response = client.post(f"/admin/users/{new_user_id}/organization", data={
                    "organization_id": str(org_id),
                    "role": "owner",
                    "csrf_token": get_csrf_token(client, "/admin/users-orgs"),
                }, follow_redirects=True)

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "Não foi possível atualizar o vínculo da organização. Nenhuma alteração foi salva." in html
        assert "synthetic failure" not in html
        assert "Traceback" not in html

        unexpected_logs = [r for r in caplog.records if "Falha inesperada" in r.message]
        assert len(unexpected_logs) == 1

        with app.app_context():
            assert OrganizationMember.query.filter_by(
                organization_id=org_id, user_id=new_user_id
            ).count() == 0

    def test_redirects_to_users_orgs_not_org_details(self, client, app, get_csrf_token):
        with app.app_context():
            org = _create_organization()
            org_id = org.id
            new_user = _create_user("usuario.link.redirect.issue41@example.com")
            new_user_id = new_user.id
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.post(f"/admin/users/{new_user_id}/organization", data={
            "organization_id": str(org_id),
            "role": "owner",
            "csrf_token": get_csrf_token(client, "/admin/users-orgs"),
        })

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/admin/users-orgs")


# Segurança - regressão mínima (não duplica toda a cobertura já existente
# em test_csrf_protection.py / test_internal_admin_membership_policy.py;
# só confirma que a nova classificação de exceção não abriu brecha) --------

class TestSecurityRegressionAcrossTheFiveRoutes:
    def test_unauthenticated_request_is_redirected_without_mutation(self, client, app):
        with app.app_context():
            org_id = _create_organization().id

        response = client.post(f"/admin/organizations/{org_id}/members", data={})

        assert response.status_code in (302, 400)
        with app.app_context():
            assert OrganizationMember.query.filter_by(organization_id=org_id).count() == 0

    def test_regular_user_cannot_add_member(self, client, app, get_csrf_token):
        with app.app_context():
            org_id = _create_organization().id
            _create_user("usuario.comum.issue41@example.com")
        _login(client, get_csrf_token, "usuario.comum.issue41@example.com")

        response = client.post(f"/admin/organizations/{org_id}/members", data={
            "user_id": str(org_id),
            "role": "member",
            "csrf_token": get_csrf_token(client),
        })

        assert response.status_code == 302
        with app.app_context():
            assert OrganizationMember.query.filter_by(organization_id=org_id).count() == 0

    def test_create_organization_without_csrf_token_returns_400(self, client, app, get_csrf_token):
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.post("/admin/organizations", data={"legal_name": "Org Sem CSRF Issue 41"})

        assert response.status_code == 400
        with app.app_context():
            assert Organization.query.filter_by(legal_name="Org Sem CSRF Issue 41").first() is None

    def test_get_is_rejected_on_all_five_mutating_routes(self, client, app, get_csrf_token):
        with app.app_context():
            org_id = _create_organization().id
            user_id = _create_user("usuario.get.issue41@example.com").id
        _create_admin_and_login(client, app, get_csrf_token)

        mutating_urls = [
            "/admin/organizations",
            f"/admin/organizations/{org_id}/members",
            f"/admin/organizations/{org_id}/members/{user_id}/role",
            f"/admin/organizations/{org_id}/members/{user_id}/remove",
            f"/admin/users/{user_id}/organization",
        ]
        for url in mutating_urls:
            assert client.get(url).status_code == 405


# Regressão explícita: grant/revoke de produto continuam no padrão seguro,
# não regrediram com a mudança nas rotas de organização (cobertura completa
# já vive em test_admin_product_access.py::TestErrorClassificationViaHTTP -
# aqui só uma reconfirmação mínima e direcionada).

class TestGrantRevokeStillSafeAfterOrganizationChanges:
    def test_grant_unexpected_failure_still_shows_generic_message(
        self, client, app, get_csrf_token, monkeypatch
    ):
        from app.services.bootstrap_service import BootstrapService

        with app.app_context():
            BootstrapService.ensure_structural_catalog()
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        with monkeypatch.context() as m:
            m.setattr(db.session, "commit", _raise_commit)
            response = client.post(f"/admin/organizations/{org_id}/products/gedo/grant", data={
                "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
            }, follow_redirects=True)

        html = response.data.decode("utf-8")
        assert "Não foi possível processar a operação de acesso a produto" in html
        assert "synthetic failure" not in html


# Validação de UUID vindo de formulário (Issue #41: add_member converte
# somente user_id; link_user_to_org converte somente organization_id -
# nos dois casos o outro identificador já chega como uuid.UUID pronto via
# conversor <uuid:...> da própria URL, então não passa por este parser).

class TestFormUuidValidation:
    @pytest.mark.parametrize("invalid_user_id", [None, "nao-e-um-uuid-valido"], ids=["ausente", "malformado"])
    def test_add_member_rejects_invalid_user_id_without_calling_service(
        self, client, app, get_csrf_token, monkeypatch, caplog, invalid_user_id
    ):
        with app.app_context():
            org_id = _create_organization().id
        _create_admin_and_login(client, app, get_csrf_token)

        def _fail_if_called(*args, **kwargs):
            raise AssertionError("add_member não deveria ser chamado com user_id inválido")

        data = {"role": "member", "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}")}
        if invalid_user_id is not None:
            data["user_id"] = invalid_user_id

        with monkeypatch.context() as m:
            m.setattr(OrganizationService, "add_member", _fail_if_called)
            with caplog.at_level("ERROR"):
                response = client.post(
                    f"/admin/organizations/{org_id}/members", data=data, follow_redirects=True
                )

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "Usuário inválido." in html
        if invalid_user_id:
            assert invalid_user_id not in html
        assert not any("Falha inesperada" in r.message for r in caplog.records)
        with app.app_context():
            assert OrganizationMember.query.filter_by(organization_id=org_id).count() == 0
            assert AuditLog.query.filter_by(action="organization.member.added").count() == 0

    @pytest.mark.parametrize("invalid_org_id", [None, "nao-e-um-uuid-valido"], ids=["ausente", "malformado"])
    def test_link_user_to_org_rejects_invalid_organization_id_without_calling_service(
        self, client, app, get_csrf_token, monkeypatch, caplog, invalid_org_id
    ):
        with app.app_context():
            new_user_id = _create_user("usuario.link.invalido.issue41@example.com").id
        _create_admin_and_login(client, app, get_csrf_token)

        def _fail_if_called(*args, **kwargs):
            raise AssertionError("add_member não deveria ser chamado com organization_id inválido")

        data = {"role": "owner", "csrf_token": get_csrf_token(client, "/admin/users-orgs")}
        if invalid_org_id is not None:
            data["organization_id"] = invalid_org_id

        with monkeypatch.context() as m:
            m.setattr(OrganizationService, "add_member", _fail_if_called)
            with caplog.at_level("ERROR"):
                response = client.post(
                    f"/admin/users/{new_user_id}/organization", data=data, follow_redirects=True
                )

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "Organização inválida." in html
        if invalid_org_id:
            assert invalid_org_id not in html
        assert not any("Falha inesperada" in r.message for r in caplog.records)
        with app.app_context():
            assert OrganizationMember.query.filter_by(user_id=new_user_id).count() == 0
            assert AuditLog.query.filter_by(action="organization.member.added").count() == 0

    def test_malformed_user_id_in_link_url_is_rejected_by_route_converter(
        self, client, app, get_csrf_token
    ):
        # Nesta rota, user_id vem do segmento <uuid:user_id> da URL, nunca
        # de formulário - um valor malformado aqui já é rejeitado pelo
        # conversor de rota do próprio Flask (404), antes de qualquer
        # código desta Issue rodar. Documentado para deixar explícito que
        # os dois pontos de entrada de link_user_to_org (URL para user_id,
        # formulário para organization_id) têm mecanismos de validação
        # diferentes.
        _create_admin_and_login(client, app, get_csrf_token)

        response = client.post("/admin/users/nao-e-um-uuid-valido/organization", data={
            "organization_id": "00000000-0000-0000-0000-000000000000",
            "role": "owner",
            "csrf_token": get_csrf_token(client, "/admin/users-orgs"),
        })

        assert response.status_code == 404

    def test_valid_uuid_string_is_converted_to_uuid_object_before_reaching_service(
        self, client, app, get_csrf_token, monkeypatch
    ):
        with app.app_context():
            org_id = _create_organization().id
            new_user_id = _create_user("usuario.conversao.issue41@example.com").id
        _create_admin_and_login(client, app, get_csrf_token)

        captured = {}
        original_add_member = OrganizationService.add_member

        def _spy_add_member(organization_id, user_id, role_name):
            captured["organization_id"] = organization_id
            captured["user_id"] = user_id
            return original_add_member(organization_id, user_id, role_name)

        with monkeypatch.context() as m:
            m.setattr(OrganizationService, "add_member", _spy_add_member)
            response = client.post(f"/admin/organizations/{org_id}/members", data={
                "user_id": str(new_user_id),
                "role": "member",
                "csrf_token": get_csrf_token(client, f"/admin/organizations/{org_id}"),
            })

        assert response.status_code == 302
        assert isinstance(captured["user_id"], uuid.UUID)
        assert captured["user_id"] == new_user_id
