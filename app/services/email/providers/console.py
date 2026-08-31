import codecs
import logging
import sys
from typing import Optional, TextIO
from .base import EmailProvider

logger = logging.getLogger(__name__)

class ConsoleEmailProvider(EmailProvider):
    """Provider de e-mail para desenvolvimento: nunca envia de verdade,
    apenas imprime o conteúdo (incluindo o código de verificação, que é a
    finalidade deste provider) no stream informado - por padrão,
    `sys.stdout` resolvido no momento do envio, não na criação do objeto.

    Cada linha é normalizada para o encoding do stream *antes* da única
    chamada a `stream.write()` - nunca há tentativa, captura de
    `UnicodeEncodeError` e nova tentativa. Um caractere não representável
    no encoding do stream vira um escape ASCII visível do Python (a forma
    exata - `\\xHH`, `\\uXXXX` ou `\\UXXXXXXXX` - depende do caractere),
    preservando o restante do conteúdo. Qualquer falha real do stream
    (`OSError`, stream fechado etc., inclusive durante `write()` ou
    `flush()`) nunca é capturada aqui e continua propagando normalmente.
    """

    def __init__(self, stream: Optional[TextIO] = None):
        self._stream = stream

    @staticmethod
    def _resolve_encoding(stream):
        """Determina um nome de encoding válido e conhecido pelo Python
        para normalizar o texto antes de escrever. Usa `stream.encoding`
        quando ele existir, for uma string e for reconhecido por
        `codecs.lookup`; caso contrário (ausente, `None`, não-string ou
        nome desconhecido) usa ASCII - o encoding mais restritivo e
        universalmente seguro, garantindo que a normalização sempre force
        o escape de qualquer caractere não-ASCII."""
        encoding = getattr(stream, "encoding", None)
        if not isinstance(encoding, str):
            return "ascii"
        try:
            codecs.lookup(encoding)
        except LookupError:
            return "ascii"
        return encoding

    @staticmethod
    def _write_line(stream, encoding, text):
        normalized = text.encode(encoding, errors="backslashreplace").decode(encoding)
        stream.write(normalized + "\n")

    def send(self, to: str, subject: str, html: str, reply_to: Optional[str] = None) -> None:
        stream = self._stream if self._stream is not None else sys.stdout
        encoding = self._resolve_encoding(stream)

        self._write_line(stream, encoding, "")
        self._write_line(stream, encoding, "=" * 50)
        self._write_line(stream, encoding, f"[DEV EMAIL] To: {to}")
        self._write_line(stream, encoding, f"Subject: {subject}")
        if reply_to:
            self._write_line(stream, encoding, f"Reply-To: {reply_to}")
        self._write_line(stream, encoding, "--- HTML CONTENT ---")
        self._write_line(stream, encoding, html)
        self._write_line(stream, encoding, "=" * 50)
        self._write_line(stream, encoding, "")

        # Sem depender de PYTHONUNBUFFERED: garante que o conteúdo chegue
        # ao stream imediatamente, mesmo quando stdout está redirecionado
        # a um arquivo (totalmente bufferizado por padrão nesse cenário).
        stream.flush()

        # Mensagem estática deliberadamente: `to` é dado dinâmico (pode
        # conter Unicode ou informação identificável) e o conteúdo
        # completo já pertence à saída explícita deste provider, não ao
        # logger.
        logger.info("[DEV] Email simulated")
