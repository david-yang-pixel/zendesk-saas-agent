"""Low-cardinality ingress metrics that never include ticket identifiers."""

from __future__ import annotations

from collections import Counter
from typing import Protocol


class IngressMetricsPort(Protocol):
    def record_webhook(self, *, action: str, reason: str) -> None: ...

    def render(self) -> str: ...


class InMemoryIngressMetrics:
    def __init__(self) -> None:
        self._webhooks: Counter[tuple[str, str]] = Counter()

    def record_webhook(self, *, action: str, reason: str) -> None:
        self._webhooks[(action, reason)] += 1

    def render(self) -> str:
        lines = [
            "# HELP casework_ingress_webhooks_total Webhook admission decisions.",
            "# TYPE casework_ingress_webhooks_total counter",
        ]
        for (action, reason), count in sorted(self._webhooks.items()):
            lines.append(
                "casework_ingress_webhooks_total"
                f'{{action="{action}",reason="{reason}"}} {count}'
            )
        return "\n".join(lines) + "\n"

