from abc import ABC, abstractmethod
from typing import Optional

class EmailProvider(ABC):
    @abstractmethod
    def send(self, to: str, subject: str, html: str, reply_to: Optional[str] = None) -> None:
        pass
