import os
from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)

class EmailProvider(ABC):
    @abstractmethod
    def send_verification_code(self, to_email: str, code: str):
        pass

class ConsoleEmailProvider(EmailProvider):
    def send_verification_code(self, to_email: str, code: str):
        # Em ambiente de desenvolvimento, exibe o código no terminal de forma destacada
        print(f"\n{'='*50}")
        print(f"📧 [DEV EMAIL] To: {to_email}")
        print(f"🔑 VERIFICATION CODE: {code}")
        print(f"{'='*50}\n")
        logger.info(f"[DEV] Verification code generated and sent to {to_email}")

class SMTPEmailProvider(EmailProvider):
    def send_verification_code(self, to_email: str, code: str):
        # TODO: Implementar envio real via SMTP, SendGrid, etc.
        raise NotImplementedError("SMTP Provider not yet implemented.")

class EmailService:
    @staticmethod
    def get_provider() -> EmailProvider:
        provider_type = os.getenv('EMAIL_PROVIDER', 'console').lower()
        if provider_type == 'console':
            app_env = os.getenv('FLASK_ENV', 'development')
            if app_env == 'production':
                raise RuntimeError("Cannot use ConsoleEmailProvider in production!")
            return ConsoleEmailProvider()
        elif provider_type == 'smtp':
            return SMTPEmailProvider()
        else:
            raise ValueError(f"Unknown EMAIL_PROVIDER: {provider_type}")

    @classmethod
    def send_verification_code(cls, to_email: str, code: str):
        provider = cls.get_provider()
        provider.send_verification_code(to_email, code)
