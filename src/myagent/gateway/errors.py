"""The one exception the gateway's routes understand.

``GatewayApp`` raises :class:`GatewayError` and only ``myagent.gateway.server``
turns it into a status code and a JSON body. Keeping the status *inside* the
exception is what lets the application layer stay free of ``http.server`` types
while still being able to say "this is a 400, not a 500".
"""

from __future__ import annotations

__all__ = ["GatewayError"]


class GatewayError(Exception):
    """A failure the browser should see as one specific 4xx/5xx response."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
