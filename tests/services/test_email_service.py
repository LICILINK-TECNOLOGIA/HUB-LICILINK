import pytest
from unittest.mock import patch
from app.services.email import EmailService
from app.services.email.providers.console import ConsoleEmailProvider
from app.services.email.providers.resend import ResendEmailProvider

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
