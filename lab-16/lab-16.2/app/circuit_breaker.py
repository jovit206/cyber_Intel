from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, TypeVar

T = TypeVar("T")
logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(RuntimeError):
    pass


@dataclass
class _BackendState:
    state: CircuitState = CircuitState.CLOSED
    failures: int = 0
    opened_at: float = 0.0
    probe_in_flight: bool = False


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        if failure_threshold < 1 or recovery_timeout <= 0:
            raise ValueError("Circuit threshold and recovery timeout must be positive.")
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._clock = clock
        self._lock = threading.RLock()
        self._backends: dict[str, _BackendState] = {}

    def state(self, backend: str) -> CircuitState:
        with self._lock:
            return self._backends.setdefault(backend, _BackendState()).state

    def call(self, backend: str, operation: Callable[..., T], *args, **kwargs) -> T:
        with self._lock:
            status = self._backends.setdefault(backend, _BackendState())
            now = self._clock()
            if status.state == CircuitState.OPEN:
                if now - status.opened_at < self.recovery_timeout:
                    raise CircuitOpenError(f"{backend} circuit is open.")
                self._transition(backend, status, CircuitState.HALF_OPEN)
            if status.state == CircuitState.HALF_OPEN:
                if status.probe_in_flight:
                    raise CircuitOpenError(f"{backend} recovery probe is already in flight.")
                status.probe_in_flight = True
        try:
            result = operation(*args, **kwargs)
        except Exception:
            with self._lock:
                status = self._backends[backend]
                status.probe_in_flight = False
                status.failures += 1
                if status.state == CircuitState.HALF_OPEN or status.failures >= self.failure_threshold:
                    status.opened_at = self._clock()
                    self._transition(backend, status, CircuitState.OPEN)
            raise
        with self._lock:
            status = self._backends[backend]
            status.failures = 0
            status.probe_in_flight = False
            self._transition(backend, status, CircuitState.CLOSED)
        return result

    @staticmethod
    def _transition(backend: str, status: _BackendState, new_state: CircuitState) -> None:
        if status.state != new_state:
            previous = status.state
            status.state = new_state
            logger.warning("Circuit for %s changed from %s to %s", backend, previous.value, new_state.value)
