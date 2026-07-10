from __future__ import annotations


class TriggerDeferredError(RuntimeError):
    def __init__(self, reason: str, *, delay_seconds: float = 30.0) -> None:
        self.reason = reason
        self.delay_seconds = max(0.0, delay_seconds)
        super().__init__(reason)


class PublicReplyDeliveryError(RuntimeError):
    def __init__(self, ticket_id: int, *, kind: str = "reply") -> None:
        label = "public ask" if kind == "ask" else "public reply"
        super().__init__(f"{label} delivery failed for ticket #{ticket_id}")
