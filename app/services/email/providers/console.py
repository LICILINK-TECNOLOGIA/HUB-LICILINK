import logging
from typing import Optional
from .base import EmailProvider

logger = logging.getLogger(__name__)

class ConsoleEmailProvider(EmailProvider):
    def send(self, to: str, subject: str, html: str, reply_to: Optional[str] = None) -> None:
        print(f"\n{'='*50}")
        print(f"📧 [DEV EMAIL] To: {to}")
        print(f"Subject: {subject}")
        if reply_to:
            print(f"Reply-To: {reply_to}")
        print("--- HTML CONTENT ---")
        print(html)
        print(f"{'='*50}\n")
        logger.info(f"[DEV] Email simulated for {to}")
