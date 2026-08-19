import logging
from flask import current_app, render_template
from .providers.console import ConsoleEmailProvider
from .providers.resend import ResendEmailProvider
from .providers.base import EmailProvider

logger = logging.getLogger(__name__)

class EmailService:
    def __init__(self):
        provider_name = current_app.config.get('EMAIL_PROVIDER', 'console').lower()
        
        if provider_name == 'console':
            self.provider: EmailProvider = ConsoleEmailProvider()
        elif provider_name == 'resend':
            self.provider = ResendEmailProvider()
        else:
            raise ValueError(f"Unknown EMAIL_PROVIDER: {provider_name}")

    def send_verification_email(self, to: str, name: str, code: str) -> None:
        subject = "Confirme seu cadastro - LiciLink"
        expiration_minutes = int(current_app.config.get('VERIFICATION_CODE_TTL', 600)) // 60
        
        html_content = render_template(
            "emails/verification.html",
            name=name,
            code=code,
            expiration_minutes=expiration_minutes
        )
        
        reply_to = current_app.config.get('EMAIL_REPLY_TO')
        self.provider.send(to=to, subject=subject, html=html_content, reply_to=reply_to)
