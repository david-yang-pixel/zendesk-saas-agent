from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from math import ceil


class AdminLoginLimiter:
    def __init__(
        self,
        *,
        per_ip_limit: int,
        per_account_limit: int,
        window_seconds: float,
        max_keys: int,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self.per_ip_limit = max(1, per_ip_limit)
        self.per_account_limit = max(self.per_ip_limit, per_account_limit)
        self.window_seconds = max(1.0, window_seconds)
        self.max_keys = max(1, max_keys)
        self.now = now_fn
        self.failures: OrderedDict[tuple[str, str], list[float]] = OrderedDict()

    def retry_after(self, *, username: str, remote_ip: str) -> int:
        now = self.now()
        ip_failures = self._active(("ip", remote_ip), now)
        account_failures = self._active(("account", username), now)
        retry_at = 0.0
        if len(ip_failures) >= self.per_ip_limit:
            retry_at = max(retry_at, ip_failures[0] + self.window_seconds)
        if len(account_failures) >= self.per_account_limit:
            retry_at = max(retry_at, account_failures[0] + self.window_seconds)
        return max(0, ceil(retry_at - now))

    def record_failure(self, *, username: str, remote_ip: str) -> None:
        now = self.now()
        for key in (("ip", remote_ip), ("account", username)):
            failures = self._active(key, now)
            failures.append(now)
            self.failures[key] = failures
            self.failures.move_to_end(key)
        while len(self.failures) > self.max_keys:
            self.failures.popitem(last=False)

    def clear_success(self, *, username: str, remote_ip: str) -> None:
        self.failures.pop(("ip", remote_ip), None)
        self.failures.pop(("account", username), None)

    def _active(self, key: tuple[str, str], now: float) -> list[float]:
        cutoff = now - self.window_seconds
        failures = [value for value in self.failures.get(key, []) if value > cutoff]
        if failures:
            self.failures[key] = failures
            self.failures.move_to_end(key)
        else:
            self.failures.pop(key, None)
        return failures
