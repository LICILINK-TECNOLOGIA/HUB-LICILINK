import resend
import logging
from typing import Optional
from flask import current_app
from .base import EmailProvider

logger = logging.getLogger(__name__)

class ResendEmailProvider(EmailProvider):
    def __init__(self):
        api_key = current_app.config.get('RESEND_API_KEY')
        if not api_key:
            raise ValueError("RESEND_API_KEY is required for ResendEmailProvider")
        resend.api_key = api_key

    def send(self, to: str, subject: str, html: str, reply_to: Optional[str] = None) -> None:
        from_email = current_app.config.get('EMAIL_FROM')
        if not from_email:
            raise ValueError("EMAIL_FROM is required for ResendEmailProvider")
        
        params = {
            "from": from_email,
            "to": to,
            "subject": subject,
            "html": html,
        }
        
        if reply_to:
            params["reply_to"] = reply_to
            
        try:
            email_response = resend.Emails.send(params)
            logger.info(f"Resend email dispatched to {to}. Response: {email_response}")
        except Exception as e:
            logger.error(f"Failed to send email via Resend to {to}: {str(e)}")
            raise RuntimeError(f"Email delivery failed: {str(e)}") from e
