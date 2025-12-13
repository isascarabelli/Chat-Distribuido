"""
Implementação do Relógio Lógico de Lamport

O Relógio de Lamport é usado para ordenação parcial de eventos em sistemas distribuídos.
Cada processo mantém um contador local que incrementa a cada evento.
Quando processos se comunicam, sincronizam seus relógios.
"""

import threading


class LamportClock:
    """
    Relógio Lógico de Lamport thread-safe para ordenação de eventos.

    Regras:
    1. Antes de executar um evento, incrementa o relógio local
    2. Ao enviar mensagem, inclui o timestamp atual
    3. Ao receber mensagem, atualiza: max(local, recebido) + 1
    """

    def __init__(self):
        self._timestamp = 0
        self._lock = threading.Lock()

    def get_time(self):
        """Retorna o timestamp atual sem modificá-lo."""
        with self._lock:
            return self._timestamp

    def incrementaRelogio(self):
        """
        Incrementa o relógio antes de um evento local.
        Retorna o novo timestamp.
        """
        with self._lock:
            self._timestamp += 1
            return self._timestamp

    def updateRelogio(self, received_timestamp):
        """
        Atualiza o relógio ao receber um evento externo.
        Aplica a regra: max(local, recebido) + 1

        Args:
            received_timestamp: timestamp recebido de outro processo

        Returns:
            O novo timestamp local
        """
        with self._lock:
            self._timestamp = max(self._timestamp, received_timestamp) + 1
            return self._timestamp
