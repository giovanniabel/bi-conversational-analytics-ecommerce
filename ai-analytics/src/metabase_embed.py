"""
Signed ("static") Metabase embed URLs.

Metabase serves an embeddable view of a dashboard or card when handed a
short-lived JWT signed with its embedding secret. Signing happens here so
the secret stays server-side — the browser only ever receives the token.

The secret is read back from the running Metabase rather than configured
in two places, so signed embedding works with no setup. An operator can
still pin one via METABASE_EMBEDDING_SECRET.
"""

from __future__ import annotations

import time
from typing import Optional

import jwt

DEFAULT_TTL_SECONDS = 60 * 60  # 1 hour


class EmbedSigner:
    """Mints signed embed URLs for a Metabase instance."""

    def __init__(self, public_url: str, secret: str):
        self._public_url = public_url.rstrip("/")
        self._secret = secret

    @property
    def available(self) -> bool:
        return bool(self._secret)

    def _sign(self, resource: dict, ttl_seconds: int) -> str:
        payload = {
            "resource": resource,
            "params": {},
            "exp": int(time.time()) + ttl_seconds,
        }
        return jwt.encode(payload, self._secret, algorithm="HS256")

    def _url(self, kind: str, token: str, titled: bool, bordered: bool, theme: Optional[str]) -> str:
        fragment = f"#bordered={str(bordered).lower()}&titled={str(titled).lower()}"
        if theme:
            fragment += f"&theme={theme}"
        return f"{self._public_url}/embed/{kind}/{token}{fragment}"

    def dashboard_url(
        self,
        dashboard_id: int,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        titled: bool = False,
        bordered: bool = False,
        theme: Optional[str] = "night",
    ) -> Optional[str]:
        if not self.available:
            return None
        token = self._sign({"dashboard": dashboard_id}, ttl_seconds)
        return self._url("dashboard", token, titled, bordered, theme)

    def card_url(
        self,
        card_id: int,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        titled: bool = True,
        bordered: bool = False,
        theme: Optional[str] = "night",
    ) -> Optional[str]:
        if not self.available:
            return None
        token = self._sign({"question": card_id}, ttl_seconds)
        return self._url("question", token, titled, bordered, theme)
