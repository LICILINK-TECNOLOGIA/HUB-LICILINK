import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect
from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect

from app.extensions import db
from app.models import Organization, OrganizationMember, Role, User


class TestUuidRoundTripOnSqlite:
    """Issue #39: regressão determinística (sem depender de sorteio de
    `uuid4()`) para a falha intermitente em que um UUID cujos 32
    caracteres hexadecimais são todos dígitos decimais (sem `a-f`) era
    coagido pelo SQLite para INTEGER/REAL, quebrando a reconstrução do
    `uuid.UUID` pelo SQLAlchemy ao expirar/recarregar o objeto. O
    `@compiles(UUID, 'sqlite')` em `tests/conftest.py` força afinidade
    TEXT (`CHAR(32)`) para a coluna, preservando a string exatamente como
    gravada - mesmos valores construídos manualmente que reproduziram o
    bug de forma controlada durante a investigação da Issue #39."""

    ALL_DIGIT_UUID = uuid.UUID(hex="86991844722847430000000000000012")
    ALL_DIGIT_UUID_ORG = uuid.UUID(hex="20000002200000022000000220000002")
    ALL_DIGIT_UUID_ROLE = uuid.UUID(hex="30000003300000033000000330000003")
    ALL_DIGIT_UUID_MEMBER = uuid.UUID(hex="40000004400000044000000440000004")

    def test_all_digit_uuid_round_trips_correctly_after_expiration(self, app):
        with app.app_context():
            user = User(name='Usuario UUID Numerico', email='usuario.uuid.numerico@example.com')
            user.id = self.ALL_DIGIT_UUID
            user.set_password('senha-sintetica-issue-39-123')
            db.session.add(user)
            db.session.commit()

            db.session.expire(user)
            assert user.id == self.ALL_DIGIT_UUID
            assert isinstance(user.id, uuid.UUID)

    def test_all_digit_uuid_round_trips_via_fresh_orm_query(self, app):
        with app.app_context():
            org = Organization(legal_name='Organizacao UUID Numerico')
            org.id = self.ALL_DIGIT_UUID
            db.session.add(org)
            db.session.commit()
            db.session.expunge(org)

            reloaded = Organization.query.filter_by(legal_name='Organizacao UUID Numerico').first()
            assert reloaded.id == self.ALL_DIGIT_UUID
            assert isinstance(reloaded.id, uuid.UUID)

    def test_declared_ddl_type_is_char32_not_uuid(self, app):
        """Distingue o tipo DECLARADO no schema (via PRAGMA table_info,
        antes de qualquer insert) do tipo de ARMAZENAMENTO em runtime
        (typeof(), verificado separadamente abaixo) - sem o override de
        `tests/conftest.py`, este valor seria a string bruta 'UUID', que
        não contém nenhuma substring reconhecida pelas regras de afinidade
        do SQLite e por isso resultaria em afinidade NUMERIC."""
        with app.app_context():
            columns = {
                row[1]: row[2]
                for row in db.session.execute(sa.text("PRAGMA table_info(users)"))
            }
            assert 'id' in columns
            assert columns['id'] == 'CHAR(32)'
            assert columns['id'] != 'UUID'

    def test_sqlite_stores_all_digit_uuid_as_text_not_number(self, app):
        """Prova via SQL textual/DBAPI (não via representação ORM) que o
        valor de ARMAZENAMENTO em runtime é TEXT: afinidade (`typeof`),
        comprimento exato, valor bruto exatamente igual ao inserido, e tipo
        Python do valor cru retornado pelo DBAPI (nunca int/float)."""
        with app.app_context():
            db.session.execute(sa.text(
                "INSERT INTO users "
                "(id, name, email, password_hash, is_active, is_internal_admin, created_at, updated_at) "
                "VALUES (:id, 'x', 'affinity.check@example.com', 'x', 1, 0, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ), {"id": self.ALL_DIGIT_UUID.hex})
            db.session.commit()

            row = db.session.execute(sa.text(
                "SELECT typeof(id), length(id), id FROM users "
                "WHERE email = 'affinity.check@example.com'"
            )).one()
            typeof_value, length_value, raw_value = row

            assert typeof_value == 'text'
            assert length_value == 32
            assert raw_value == self.ALL_DIGIT_UUID.hex
            assert isinstance(raw_value, str)
            assert not isinstance(raw_value, (int, float))

    def test_pk_and_fk_round_trip_across_related_entities(self, app):
        """Cobertura enxuta de PK+FK usando UUIDs somente-dígitos distintos
        para User/Organization/Role/OrganizationMember - não replica a
        suíte completa de membership, apenas confirma que PKs e FKs
        sobrevivem ao commit + expiração de sessão e que o relacionamento
        continua navegável/consultável."""
        with app.app_context():
            role = Role(name='owner-issue-39', description='Role sintetico Issue #39')
            role.id = self.ALL_DIGIT_UUID_ROLE

            org = Organization(legal_name='Organizacao PK FK Issue 39')
            org.id = self.ALL_DIGIT_UUID_ORG

            user = User(name='Usuario PK FK Issue 39', email='usuario.pkfk.issue39@example.com')
            user.id = self.ALL_DIGIT_UUID
            user.set_password('senha-sintetica-issue-39-pkfk')

            db.session.add_all([role, org, user])
            db.session.flush()

            member = OrganizationMember(
                user_id=user.id,
                organization_id=org.id,
                role_id=role.id,
            )
            member.id = self.ALL_DIGIT_UUID_MEMBER
            db.session.add(member)
            db.session.commit()
            db.session.expire_all()

            reloaded = OrganizationMember.query.filter_by(id=self.ALL_DIGIT_UUID_MEMBER).first()

            assert isinstance(reloaded.id, uuid.UUID)
            assert reloaded.id == self.ALL_DIGIT_UUID_MEMBER
            assert isinstance(reloaded.user_id, uuid.UUID)
            assert reloaded.user_id == self.ALL_DIGIT_UUID
            assert isinstance(reloaded.organization_id, uuid.UUID)
            assert reloaded.organization_id == self.ALL_DIGIT_UUID_ORG
            assert isinstance(reloaded.role_id, uuid.UUID)
            assert reloaded.role_id == self.ALL_DIGIT_UUID_ROLE

            assert reloaded.user.id == self.ALL_DIGIT_UUID
            assert reloaded.organization.id == self.ALL_DIGIT_UUID_ORG
            assert reloaded.role.id == self.ALL_DIGIT_UUID_ROLE


class TestUuidCompilationPerDialect:
    """Issue #39: confirma, apenas via compilação de tipo em memória (sem
    conectar a nenhum servidor PostgreSQL), que o `@compiles(UUID, 'sqlite')`
    registrado em `tests/conftest.py` afeta somente o dialeto SQLite e não
    substitui/vaza para a compilação nativa do dialeto PostgreSQL."""

    def test_uuid_type_compiles_differently_per_dialect(self):
        uuid_type = PGUUID(as_uuid=True)

        assert uuid_type.compile(dialect=sqlite_dialect()) == 'CHAR(32)'
        assert uuid_type.compile(dialect=postgresql_dialect()) == 'UUID'
