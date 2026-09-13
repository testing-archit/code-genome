import time
from collections import deque
from threading import Lock


class FixedWindowLimiter:
    def __init__(self, *, maximum_keys: int = 10_000) -> None:
        self.maximum_keys = maximum_keys
        self._requests: dict[str, deque[float]] = {}
        self._lock = Lock()

    def allow(self, key: str, limit: int, *, now: float | None = None) -> bool:
        observed = time.monotonic() if now is None else now
        cutoff = observed - 60
        with self._lock:
            requests = self._requests.setdefault(key, deque())
            while requests and requests[0] <= cutoff:
                requests.popleft()
            if len(requests) >= limit:
                return False
            requests.append(observed)
            if len(self._requests) > self.maximum_keys:
                self._requests = {
                    candidate: values
                    for candidate, values in self._requests.items()
                    if values and values[-1] > cutoff
                }
            return True
