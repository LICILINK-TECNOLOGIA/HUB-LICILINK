import pytest
from unittest.mock import patch
from app.models import User, PendingEmailVerification
from app.extensions import db

@patch('app.services.auth_service.EmailService.send_verification_email')
def test_registration_flow_with_mocked_email(mock_send, client, app, get_csrf_token):
    # 1. Start Registration
    response = client.post('/register', data={
        'name': 'Integration Test 2',
        'email': 'test_integration2@licilink.com.br',
        'password': 'Password123!',
        'password_confirm': 'Password123!',
        'csrf_token': get_csrf_token(client, '/register'),
    })
    
    # The route returns 200 with HTML since it flashes a message, or 302
    assert response.status_code in (200, 302)
    
    # 2. Check if EmailService was called
    mock_send.assert_called_once()
    kwargs = mock_send.call_args[1]
    assert kwargs['to'] == 'test_integration2@licilink.com.br'
    assert kwargs['name'] == 'Integration Test 2'
    code = kwargs['code']
    
    # 3. Verify Email
    pending = PendingEmailVerification.query.filter_by(email='test_integration2@licilink.com.br').first()
    assert pending is not None
    
    response = client.post(f'/verify', data={
        'code': code,
        'csrf_token': get_csrf_token(client, '/verify'),
    })
    
    assert response.status_code in (200, 302)
    
    # 4. Check User creation
    user = User.query.filter_by(email='test_integration2@licilink.com.br').first()
    assert user is not None
    assert user.email_verified_at is not None

@patch('app.services.auth_service.EmailService.send_verification_email')
def test_registration_flow_email_failure(mock_send, client, app, get_csrf_token):
    mock_send.side_effect = RuntimeError("Email delivery failed: API Error")

    # Start Registration
    response = client.post('/register', data={
        'name': 'Integration Test 3',
        'email': 'test_integration3@licilink.com.br',
        'password': 'Password123!',
        'password_confirm': 'Password123!',
        'csrf_token': get_csrf_token(client, '/register'),
    })
    
    # Pending should still exist so user can try to resend
    pending = PendingEmailVerification.query.filter_by(email='test_integration3@licilink.com.br').first()
    assert pending is not None
