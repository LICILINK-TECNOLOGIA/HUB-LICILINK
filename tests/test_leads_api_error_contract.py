import json
import uuid
from types import SimpleNamespace

import pytest
from flask_sqlalchemy.query import Query as _FlaskSAQuery
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Lead
from app.services.lead_service import (
    LEAD_OPERATION_ERROR_MESSAGE,
    LeadOperationError,
    LeadService,
)

SYNTHETIC_API_KEY = "chave-sintetica-issue-47"


def _raise(*args, **kwargs):
    raise RuntimeError("synthetic-failure-issue-47")


def _raise_lead_operation_error(*args, **kwargs):
    # Texto deliberadamente diferente de `LEAD_OPERATION_ERROR_MESSAGE`:
    # `LeadOperationError` continua sendo um `ValueError` que aceitaria
    # qualquer texto - a rota não pode confiar implicitamente em `str(e)`
    # para montar a resposta pública (Issue #47, correção pós-revisão).
    raise LeadOperationError("synthetic-private-detail")


def _valid_payload(**overrides):
    payload = {
        "idempotency_key": str(uuid.uuid4()),
        "name": "Lead Issue 47",
        "email": "lead.issue47@example.com",
    }
    payload.update(overrides)
    return payload


def _auth_headers(key=SYNTHETIC_API_KEY):
    return {"Authorization": f"Bearer {key}"}


# Validação de payload (rota) --------------------------------------------------

class TestCreateLeadPayloadValidation:
    @pytest.fixture(autouse=True)
    def _synthetic_api_key(self, monkeypatch):
        monkeypatch.setenv("HUB_API_KEY", SYNTHETIC_API_KEY)

    @pytest.mark.parametrize(
        "request_kwargs",
        [
            pytest.param({}, id="corpo_ausente"),
            pytest.param(
                {"data": "idempotency_key=x&name=y&email=z", "content_type": "application/x-www-form-urlencoded"},
                id="content_type_incorreto",
            ),
            pytest.param(
                {"data": "{invalido", "content_type": "application/json"},
                id="json_malformado",
            ),
            pytest.param({"json": [1, 2, 3]}, id="json_array_top_level"),
            pytest.param(
                {"data": json.dumps("apenas uma string"), "content_type": "application/json"},
                id="json_string_top_level",
            ),
            pytest.param(
                {"data": json.dumps(123), "content_type": "application/json"},
                id="json_numero_top_level",
            ),
        ],
    )
    def test_malformed_or_non_object_body_returns_400(self, client, request_kwargs):
        response = client.post("/api/v1/leads", headers=_auth_headers(), **request_kwargs)
        assert response.status_code == 400
        assert response.content_type == "application/json"
        assert response.get_json() == {"error": "Invalid payload"}

    @pytest.mark.parametrize("field", ["idempotency_key", "name", "email"])
    @pytest.mark.parametrize(
        "make_invalid",
        [
            pytest.param(lambda payload, field: payload.pop(field), id="ausente"),
            pytest.param(lambda payload, field: payload.__setitem__(field, None), id="nulo"),
            pytest.param(lambda payload, field: payload.__setitem__(field, 123), id="nao_string"),
            pytest.param(lambda payload, field: payload.__setitem__(field, ""), id="vazio"),
            pytest.param(lambda payload, field: payload.__setitem__(field, "   "), id="so_espacos"),
        ],
    )
    def test_invalid_required_field_returns_400_and_persists_nothing(self, client, field, make_invalid):
        payload = _valid_payload()
        make_invalid(payload, field)

        response = client.post("/api/v1/leads", json=payload, headers=_auth_headers())

        assert response.status_code == 400
        assert response.content_type == "application/json"
        assert response.get_json() == {"error": "Invalid payload"}
        assert Lead.query.count() == 0

    def test_valid_fields_with_surrounding_whitespace_reach_service_unmodified(self, client, monkeypatch):
        # `_required_string` usa `strip()` só para decidir se o campo é
        # válido - o valor original (com espaços) deve seguir intacto para
        # o service, nunca normalizado pela rota. Usa um spy no lugar do
        # service real para não depender (nem precisar) de persistência.
        received_kwargs = {}

        def _spy_process_lead(**kwargs):
            received_kwargs.update(kwargs)
            return SimpleNamespace(id=1), True

        payload = _valid_payload(
            idempotency_key="  " + str(uuid.uuid4()) + "  ",
            name="  Lead Com Espacos  ",
            email="  lead.espacos.issue47@example.com  ",
        )

        with monkeypatch.context() as m:
            m.setattr(LeadService, "process_lead", _spy_process_lead)
            response = client.post("/api/v1/leads", json=payload, headers=_auth_headers())

        assert response.status_code == 201
        assert received_kwargs["idempotency_key"] == payload["idempotency_key"]
        assert received_kwargs["name"] == payload["name"]
        assert received_kwargs["email"] == payload["email"]
        assert Lead.query.count() == 0


# LeadService.process_lead - núcleo transacional -------------------------------

class TestProcessLeadCore:
    def test_creates_new_lead(self, app):
        with app.app_context():
            lead, created = LeadService.process_lead(
                idempotency_key=str(uuid.uuid4()),
                name="Lead Novo",
                email="novo.issue47@example.com",
            )

            assert created is True
            assert lead is not None
            assert lead.id is not None
            assert Lead.query.count() == 1

    def test_genuine_duplicate_returns_existing_lead_without_creating_second_row(self, app):
        with app.app_context():
            key = str(uuid.uuid4())
            first, first_created = LeadService.process_lead(
                idempotency_key=key, name="Lead Original", email="original.issue47@example.com"
            )
            second, second_created = LeadService.process_lead(
                idempotency_key=key, name="Lead Repetido", email="repetido.issue47@example.com"
            )

            assert first_created is True
            assert second_created is False
            assert second.id == first.id
            assert Lead.query.count() == 1

    def test_integrity_error_with_matching_row_is_treated_as_idempotent(self, app, monkeypatch):
        with app.app_context():
            key = str(uuid.uuid4())
            existing, _ = LeadService.process_lead(
                idempotency_key=key, name="Lead Existente", email="existente.issue47@example.com"
            )

            # Simula uma corrida de idempotência: o commit otimista falha com
            # IntegrityError mesmo já existindo o registro (cenário que, em
            # produção, ocorreria por uma inserção concorrente entre o
            # insert e o commit desta chamada).
            with monkeypatch.context() as m:
                m.setattr(db.session, "commit", lambda: (_ for _ in ()).throw(IntegrityError("synthetic", {}, Exception("synthetic"))))
                lead, created = LeadService.process_lead(
                    idempotency_key=key, name="Lead Concorrente", email="concorrente.issue47@example.com"
                )

            assert created is False
            assert lead.id == existing.id
            assert Lead.query.count() == 1

    def test_integrity_error_without_matching_row_raises_operation_error(self, app):
        with app.app_context():
            key = str(uuid.uuid4())

            with pytest.raises(LeadOperationError) as exc_info:
                # `name=None` viola a constraint NOT NULL do model - IntegrityError
                # não relacionado à unicidade de idempotency_key, e nenhum lead
                # com essa chave chega a existir para a consulta de reconciliação
                # encontrar (Issue #47 - reprodução do bug original).
                LeadService.process_lead(idempotency_key=key, name=None, email="semnome.issue47@example.com")

            assert str(exc_info.value) == LEAD_OPERATION_ERROR_MESSAGE
            assert isinstance(exc_info.value.__cause__, IntegrityError)
            assert Lead.query.filter_by(idempotency_key=key).count() == 0

    def test_non_integrity_error_on_commit_rolls_back_and_preserves_cause(self, app, monkeypatch):
        with app.app_context():
            key = str(uuid.uuid4())

            with monkeypatch.context() as m:
                m.setattr(db.session, "commit", _raise)
                with pytest.raises(LeadOperationError) as exc_info:
                    LeadService.process_lead(
                        idempotency_key=key, name="Lead Falha Commit", email="falhacommit.issue47@example.com"
                    )

            assert isinstance(exc_info.value.__cause__, RuntimeError)
            assert str(exc_info.value) == LEAD_OPERATION_ERROR_MESSAGE
            assert Lead.query.filter_by(idempotency_key=key).count() == 0

    def test_query_failure_after_rollback_raises_operation_error_with_query_cause(self, app, monkeypatch):
        with app.app_context():
            key = str(uuid.uuid4())
            LeadService.process_lead(
                idempotency_key=key, name="Lead Original", email="original2.issue47@example.com"
            )

            with monkeypatch.context() as m:
                # A consulta de reconciliação pós-rollback (após o IntegrityError
                # da chave duplicada) também falha - deve virar LeadOperationError
                # com a falha da CONSULTA como causa, nunca o IntegrityError
                # original, e nunca (None, False).
                m.setattr(_FlaskSAQuery, "filter_by", _raise)
                with pytest.raises(LeadOperationError) as exc_info:
                    LeadService.process_lead(
                        idempotency_key=key, name="Lead Repetido", email="repetido2.issue47@example.com"
                    )

            assert isinstance(exc_info.value.__cause__, RuntimeError)
            assert str(exc_info.value) == LEAD_OPERATION_ERROR_MESSAGE
            assert Lead.query.filter_by(idempotency_key=key).count() == 1

    def test_session_usable_after_rollback(self, app, monkeypatch):
        with app.app_context():
            with monkeypatch.context() as m:
                m.setattr(db.session, "commit", _raise)
                with pytest.raises(LeadOperationError):
                    LeadService.process_lead(
                        idempotency_key=str(uuid.uuid4()),
                        name="Lead Falha Sessao",
                        email="falhasessao.issue47@example.com",
                    )

            # `commit` restaurado (saída do `with monkeypatch.context()`) - a
            # sessão deve continuar utilizável para uma operação subsequente
            # bem-sucedida, sem exigir nenhuma limpeza manual adicional.
            lead, created = LeadService.process_lead(
                idempotency_key=str(uuid.uuid4()),
                name="Lead Depois Da Falha",
                email="depoisdafalha.issue47@example.com",
            )
            assert created is True
            assert Lead.query.count() == 1


# Contrato HTTP de falha --------------------------------------------------------

class TestCreateLeadFailureContracts:
    @pytest.fixture(autouse=True)
    def _synthetic_api_key(self, monkeypatch):
        monkeypatch.setenv("HUB_API_KEY", SYNTHETIC_API_KEY)

    def test_lead_operation_error_returns_500_json_and_logs_exactly_once(self, client, monkeypatch, caplog):
        with monkeypatch.context() as m:
            m.setattr(LeadService, "process_lead", _raise_lead_operation_error)
            with caplog.at_level("ERROR"):
                response = client.post("/api/v1/leads", json=_valid_payload(), headers=_auth_headers())

        assert response.status_code == 500
        assert response.content_type == "application/json"
        assert response.is_json
        assert response.get_json() == {"error": LEAD_OPERATION_ERROR_MESSAGE}

        body_text = response.get_data(as_text=True)
        assert "synthetic-private-detail" not in body_text
        assert "Traceback" not in body_text

        unexpected_logs = [r for r in caplog.records if "Falha inesperada" in r.message]
        assert len(unexpected_logs) == 1

    def test_residual_exception_returns_500_json_generic_message_and_logs_exactly_once(
        self, client, monkeypatch, caplog
    ):
        with monkeypatch.context() as m:
            m.setattr(LeadService, "process_lead", _raise)
            with caplog.at_level("ERROR"):
                response = client.post("/api/v1/leads", json=_valid_payload(), headers=_auth_headers())

        assert response.status_code == 500
        assert response.content_type == "application/json"
        assert response.get_json() == {"error": LEAD_OPERATION_ERROR_MESSAGE}

        body_text = response.get_data(as_text=True)
        assert "synthetic-failure-issue-47" not in body_text
        assert "Traceback" not in body_text

        unexpected_logs = [r for r in caplog.records if "Falha inesperada" in r.message]
        assert len(unexpected_logs) == 1

    def test_get_method_not_allowed(self, client):
        response = client.get("/api/v1/leads", headers=_auth_headers())
        assert response.status_code == 405
