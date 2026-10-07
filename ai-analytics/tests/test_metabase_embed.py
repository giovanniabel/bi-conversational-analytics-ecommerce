"""
Tests for signed Metabase embed URLs.

The token is what grants a browser access to a dashboard, so the payload
shape and the no-secret fallback both matter.
"""

import time

import jwt

from src.metabase_embed import EmbedSigner

SECRET = "0123456789abcdef0123456789abcdef"


def token_from(url: str) -> str:
    """Pull the JWT out of .../embed/<kind>/<token>#fragment."""
    return url.split("#")[0].rsplit("/", 1)[1]


class TestAvailability:
    def test_without_secret_no_url_is_minted(self):
        signer = EmbedSigner(public_url="http://localhost:3000", secret="")

        assert signer.available is False
        assert signer.dashboard_url(1) is None
        assert signer.card_url(1) is None

    def test_with_secret_it_is_available(self):
        assert EmbedSigner("http://localhost:3000", SECRET).available is True


class TestDashboardUrl:
    def test_token_carries_the_dashboard_resource(self):
        signer = EmbedSigner("http://localhost:3000", SECRET)

        url = signer.dashboard_url(42)
        claims = jwt.decode(token_from(url), SECRET, algorithms=["HS256"])

        assert claims["resource"] == {"dashboard": 42}
        assert claims["params"] == {}

    def test_token_expires(self):
        signer = EmbedSigner("http://localhost:3000", SECRET)

        url = signer.dashboard_url(42, ttl_seconds=120)
        claims = jwt.decode(token_from(url), SECRET, algorithms=["HS256"])

        assert 100 < claims["exp"] - int(time.time()) <= 120

    def test_expired_token_is_rejected_by_verification(self):
        signer = EmbedSigner("http://localhost:3000", SECRET)

        url = signer.dashboard_url(42, ttl_seconds=-10)

        try:
            jwt.decode(token_from(url), SECRET, algorithms=["HS256"])
            raise AssertionError("expected an expired-signature error")
        except jwt.ExpiredSignatureError:
            pass

    def test_url_points_at_the_browser_facing_host(self):
        signer = EmbedSigner("http://metabase.example.com", SECRET)

        url = signer.dashboard_url(42)

        assert url.startswith("http://metabase.example.com/embed/dashboard/")

    def test_display_options_land_in_the_fragment(self):
        signer = EmbedSigner("http://localhost:3000", SECRET)

        url = signer.dashboard_url(42, titled=True, bordered=False, theme="night")

        fragment = url.split("#")[1]
        assert "titled=true" in fragment
        assert "bordered=false" in fragment
        assert "theme=night" in fragment


class TestCardUrl:
    def test_token_carries_the_question_resource(self):
        signer = EmbedSigner("http://localhost:3000", SECRET)

        url = signer.card_url(7)
        claims = jwt.decode(token_from(url), SECRET, algorithms=["HS256"])

        assert claims["resource"] == {"question": 7}
        assert "/embed/question/" in url

    def test_a_different_secret_cannot_verify_the_token(self):
        url = EmbedSigner("http://localhost:3000", SECRET).card_url(7)

        try:
            jwt.decode(token_from(url), "f" * 32, algorithms=["HS256"])
            raise AssertionError("expected a signature verification failure")
        except jwt.InvalidSignatureError:
            pass
