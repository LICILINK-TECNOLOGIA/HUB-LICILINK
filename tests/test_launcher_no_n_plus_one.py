"""Issue #71 (revisão técnica): a renderização do launcher (`GET /`)
combina `AccessService.get_organization_products` (2 queries: todos os
produtos + vínculos da organização) com
`OrganizationProductInstallationService.list_installations_by_organization`
(2 queries: vínculos + instalações correspondentes, mesmo padrão já
usado por `app/blueprints/admin.py::org_details` desde a #68) - nunca
uma consulta por produto.

Em vez de fixar uma contagem absoluta (frágil a qualquer consulta
interna legítima adicionada no futuro), este teste mede o número real de
`SELECT`s disparados (via `before_cursor_execute`, mesmo padrão já usado
por `tests/test_product_launch_code_service.py::test_no_extra_installation_query_after_resolution`)
para um cenário com poucos produtos e um com vários, e comprova que o
número de consultas NÃO CRESCE com a quantidade de produtos."""
from datetime import datetime

from sqlalchemy import event

from app.extensions import db
from app.models import (
    Organization,
    OrganizationProduct,
    OrganizationProductInstallation,
    Product,
    User,
)
from app.services.organization_service import OrganizationService

SYNTHETIC_PASSWORD = "senha-sintetica-issue-71-n1"


def _create_user(email):
    user = User(name="Usuario Issue 71 N+1", email=email, email_verified_at=datetime.utcnow())
    user.set_password(SYNTHETIC_PASSWORD)
    db.session.add(user)
    db.session.commit()
    return user


def _create_organization(legal_name):
    org = Organization(legal_name=legal_name, is_active=True)
    db.session.add(org)
    db.session.commit()
    return org


def _create_launchable_product_for_org(org, index, *, group):
    product = Product(
        code=f"produto-issue71-n1-{group}-{index}",
        name=f"Produto N+1 Issue 71 {group} #{index}",
        description="Descricao",
        url=f"https://produto-n1-{group}-{index}.local",
    )
    db.session.add(product)
    db.session.commit()

    org_product = OrganizationProduct(organization_id=org.id, product_id=product.id, status="active")
    db.session.add(org_product)
    db.session.commit()

    installation = OrganizationProductInstallation(
        organization_product_id=org_product.id, url=f"https://instalacao-n1-{index}.local", is_active=True,
    )
    db.session.add(installation)
    db.session.commit()


def _login(client, get_csrf_token, email):
    return client.post("/login", data={
        "email": email,
        "password": SYNTHETIC_PASSWORD,
        "csrf_token": get_csrf_token(client),
    })


def _count_launcher_get_selects(client):
    statements = []

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if statement.strip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", _before_cursor_execute)
    try:
        response = client.get("/")
        assert response.status_code == 200
    finally:
        event.remove(db.engine, "before_cursor_execute", _before_cursor_execute)

    return len(statements)


class TestLauncherQueryCountDoesNotGrowWithProductCount:
    def test_select_count_is_the_same_for_few_and_many_products(self, client, app, get_csrf_token):
        with app.app_context():
            org_few = _create_organization("Organizacao Issue 71 Poucos Produtos")
            user_few = _create_user("poucos.produtos.issue71@example.com")
            OrganizationService.add_member(org_few.id, user_few.id, "member")
            for i in range(1):
                _create_launchable_product_for_org(org_few, i, group="few")

        _login(client, get_csrf_token, "poucos.produtos.issue71@example.com")
        select_count_few = _count_launcher_get_selects(client)

        with app.app_context():
            org_many = _create_organization("Organizacao Issue 71 Muitos Produtos")
            user_many = _create_user("muitos.produtos.issue71@example.com")
            OrganizationService.add_member(org_many.id, user_many.id, "member")
            for i in range(10):
                _create_launchable_product_for_org(org_many, i, group="many")

        _login(client, get_csrf_token, "muitos.produtos.issue71@example.com")
        select_count_many = _count_launcher_get_selects(client)

        # O número de SELECTs não cresce com a quantidade de produtos -
        # nunca uma consulta por produto. Não fixa o valor absoluto (ex.:
        # "exatamente 6"), só a ausência de crescimento entre os dois
        # cenários.
        assert select_count_many == select_count_few
