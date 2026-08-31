import sys

import pytest
from unittest.mock import patch
from app.services.email import EmailService
from app.services.email.providers.console import ConsoleEmailProvider
from app.services.email.providers.resend import ResendEmailProvider

_ENCODING_ABSENT = object()


class RecordingStream:
    """Stream em memória que registra cada chamada a write() (uma entrada
    por linha) e conta flush() - não valida nem rejeita nenhum conteúdo,
    porque a normalização acontece inteiramente dentro do provider, antes
    de qualquer write(). Nenhum recurso de I/O real do sistema operacional
    é usado - o encoding "real" do terminal nunca é tocado."""

    def __init__(self, encoding=_ENCODING_ABSENT):
        if encoding is not _ENCODING_ABSENT:
            self.encoding = encoding
        self.writes = []
        self.flush_count = 0

    def write(self, text):
        self.writes.append(text)

    def flush(self):
        self.flush_count += 1

    def getvalue(self):
        return "".join(self.writes)


class FalsyRecordingStream(RecordingStream):
    """Um stream customizado válido, porém "falsy" em bool() - usado para
    provar que a resolução do stream padrão distingue `None` de um stream
    real que apenas avalia como falso."""

    def __len__(self):
        return 0


class FailingWriteStream:
    """Simula uma falha real de I/O em write() - deve sempre propagar,
    nunca ser capturada pelo provider."""

    encoding = "utf-8"

    def write(self, text):
        raise OSError("disk full (simulado)")

    def flush(self):
        pass


class FailingFlushStream:
    """Simula uma falha real de I/O em flush() - deve sempre propagar."""

    encoding = "utf-8"

    def __init__(self):
        self.writes = []

    def write(self, text):
        self.writes.append(text)

    def flush(self):
        raise OSError("flush failed (simulado)")


def test_email_service_initializes_console_provider(app):
    app.config['EMAIL_PROVIDER'] = 'console'
    service = EmailService()
    assert isinstance(service.provider, ConsoleEmailProvider)

def test_email_service_initializes_resend_provider(app):
    app.config['EMAIL_PROVIDER'] = 'resend'
    app.config['RESEND_API_KEY'] = 'fake_key'
    service = EmailService()
    assert isinstance(service.provider, ResendEmailProvider)

def test_email_service_unknown_provider(app):
    app.config['EMAIL_PROVIDER'] = 'unknown'
    with pytest.raises(ValueError, match="Unknown EMAIL_PROVIDER: unknown"):
        EmailService()

@patch('app.services.email.providers.resend.resend.Emails.send')
def test_resend_provider_sends_email(mock_send, app):
    app.config['EMAIL_PROVIDER'] = 'resend'
    app.config['RESEND_API_KEY'] = 'fake_key'
    app.config['EMAIL_FROM'] = 'test@example.com'

    service = EmailService()

    # Test sending
    service.send_verification_email("user@example.com", "User Name", "123456")

    # Verify the mock was called correctly
    mock_send.assert_called_once()
    call_args = mock_send.call_args[0][0]
    assert call_args['from'] == 'test@example.com'
    assert call_args['to'] == 'user@example.com'
    assert call_args['subject'] == "Confirme seu cadastro - LiciLink"
    assert "123456" in call_args['html']
    assert "User Name" in call_args['html']

@patch('app.services.email.providers.resend.resend.Emails.send')
def test_resend_provider_handles_exception(mock_send, app):
    app.config['EMAIL_PROVIDER'] = 'resend'
    app.config['RESEND_API_KEY'] = 'fake_key'
    app.config['EMAIL_FROM'] = 'test@example.com'

    mock_send.side_effect = Exception("API Error")

    service = EmailService()
    with pytest.raises(RuntimeError, match="Email delivery failed: API Error"):
        service.send_verification_email("user@example.com", "User Name", "123456")


class TestConsoleProviderEncodingRobustness:
    """Issue #31: ConsoleEmailProvider.send() nunca pode falhar apenas
    porque o stream de saída não representa algum caractere - cada linha é
    normalizada para o encoding do stream antes da única chamada a
    write(), nunca por tentativa/captura/nova tentativa."""

    @pytest.mark.parametrize(
        "encoding,expect_raw_emoji",
        [
            ("utf-8", True),
            ("cp1252", False),
            ("ascii", False),
        ],
        ids=["utf-8", "cp1252", "ascii"],
    )
    def test_normalizes_per_declared_encoding(self, encoding, expect_raw_emoji):
        stream = RecordingStream(encoding=encoding)
        provider = ConsoleEmailProvider(stream=stream)

        provider.send(to="user@example.com", subject="Bem-vindo 🎉", html="<p>Código: 123456</p>")

        output = stream.getvalue()
        assert "user@example.com" in output
        assert "123456" in output
        assert ("🎉" in output) is expect_raw_emoji

    @pytest.mark.parametrize(
        "encoding",
        [_ENCODING_ABSENT, None, "not-a-real-codec-xyz"],
        ids=["absent", "none", "invalid-name"],
    )
    def test_falls_back_to_ascii_when_encoding_is_unusable(self, encoding):
        stream = RecordingStream(encoding=encoding)
        provider = ConsoleEmailProvider(stream=stream)

        provider.send(to="user@example.com", subject="Bem-vindo 🎉", html="<p>Código: 123456</p>")

        output = stream.getvalue()
        assert "user@example.com" in output
        assert "123456" in output
        assert "🎉" not in output  # tratado como ASCII, o mais restritivo

    def test_writes_exactly_once_per_line_without_reply_to(self):
        stream = RecordingStream(encoding="ascii")
        provider = ConsoleEmailProvider(stream=stream)

        provider.send(to="user@example.com", subject="Bem-vindo 🎉", html="<p>Código: 123456</p>")

        # linha vazia, "====", To, Subject, "---HTML---", html, "====", linha vazia
        assert len(stream.writes) == 8

    def test_writes_exactly_once_per_line_with_reply_to(self):
        stream = RecordingStream(encoding="ascii")
        provider = ConsoleEmailProvider(stream=stream)

        provider.send(
            to="user@example.com",
            subject="Assunto",
            html="<p>Código: 123456</p>",
            reply_to="reply@example.com",
        )

        assert len(stream.writes) == 9  # as 8 de cima + Reply-To

    def test_real_write_failure_propagates(self):
        provider = ConsoleEmailProvider(stream=FailingWriteStream())

        with pytest.raises(OSError, match="disk full"):
            provider.send(to="user@example.com", subject="Assunto", html="<p>Código: 123456</p>")

    def test_real_flush_failure_propagates(self):
        provider = ConsoleEmailProvider(stream=FailingFlushStream())

        with pytest.raises(OSError, match="flush failed"):
            provider.send(to="user@example.com", subject="Assunto", html="<p>Código: 123456</p>")

    def test_flush_called_exactly_once_on_success(self):
        stream = RecordingStream(encoding="utf-8")
        provider = ConsoleEmailProvider(stream=stream)

        provider.send(to="user@example.com", subject="Assunto", html="<p>Código: 123456</p>")

        assert stream.flush_count == 1

    def test_default_stream_resolves_to_current_sys_stdout(self, monkeypatch):
        fake_stdout = RecordingStream(encoding="ascii")
        monkeypatch.setattr(sys, "stdout", fake_stdout)

        provider = ConsoleEmailProvider()  # sem stream explícito
        provider.send(to="user@example.com", subject="Bem-vindo 🎉", html="<p>Código: 123456</p>")

        assert "123456" in fake_stdout.getvalue()
        # monkeypatch restaura sys.stdout automaticamente ao final do teste.

    def test_falsy_custom_stream_is_not_replaced_by_stdout(self, monkeypatch):
        fake_stdout = RecordingStream(encoding="utf-8")
        monkeypatch.setattr(sys, "stdout", fake_stdout)

        falsy_stream = FalsyRecordingStream(encoding="utf-8")
        assert not falsy_stream  # confirma que o próprio stream é "falsy"

        provider = ConsoleEmailProvider(stream=falsy_stream)
        provider.send(to="user@example.com", subject="Assunto", html="<p>Código: 123456</p>")

        assert "123456" in falsy_stream.getvalue()
        assert fake_stdout.getvalue() == ""  # nada foi escrito no sys.stdout trocado


class TestRegistrationNotBlockedByConsoleEncoding:
    """Issue #31: o fluxo público de registro não pode ser bloqueado só
    porque o stdout do processo não representa algum caractere."""

    def test_start_registration_not_blocked_by_restrictive_stdout_encoding(self, app, monkeypatch):
        from app.services.auth_service import AuthService

        fake_stdout = RecordingStream(encoding="ascii")
        monkeypatch.setattr(sys, "stdout", fake_stdout)
        app.config['EMAIL_PROVIDER'] = 'console'

        with app.app_context():
            pending = AuthService.start_registration(
                "Usuário Encoding Teste",
                "encoding.teste@example.com",
                "senha-sintetica-encoding-123",
            )
            assert pending is not None
            assert pending.email == "encoding.teste@example.com"
        # A prova de que o encoding não bloqueou mais nada é o próprio
        # sucesso da chamada acima (start_registration levantaria
        # ValueError na etapa de envio de e-mail se o provider ainda
        # quebrasse) - não é necessário inspecionar o conteúdo do stream
        # aqui, e o código de verificação real não é registrado neste teste.
