# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Browser sign-in for the HTTP transport, and the token GSF gets afterwards.

Without this, every caller of an ``http`` deployment has to mint a GSF API token
by hand before it can call anything. FastMCP can run the sign-in instead: it
advertises an authorization server, the client opens a browser, and the user
authenticates against whichever OIDC provider the deployment names.

The subtle part is which token then reaches GSF. FastMCP does not hand the
client its upstream tokens — it mints its own JWT and keeps the upstream set
server-side, so the ``Authorization`` header arriving on a tool call is a
*FastMCP* token. GSF cannot verify that: ``frontend/auth/bearer.ts`` checks a
bearer against the SSO provider's JWKS, and a FastMCP-issued token is not signed
by that provider. Forwarding the incoming header, which is the right move for a
hand-minted GSF token, would fail here for every request.

So the upstream id token has to be carried deliberately.
:meth:`OAuthProxy._extract_upstream_claims` is the documented hook for this: it
receives the provider's full token response and returns claims to embed in the
FastMCP JWT, which :meth:`load_access_token` later restores onto the validated
token. Capturing ``id_token`` there makes it readable from a tool through the
ordinary ``get_access_token()`` dependency, with no reach into private storage.

The id token is the right choice over the access token, which is what FastMCP
otherwise exposes. An id token is always a JWT verifiable through the provider's
JWKS and always carries the identity claims GSF resolves a user by, whereas
access tokens are frequently opaque, or scoped to an audience that makes them
unverifiable by a third party. GSF deliberately does not check the audience
claim, so an id token minted for *this* server's client id still resolves.

Verifying that id token needs one more thing than FastMCP assumes. Its
:class:`JWTVerifier` is fixed to a single algorithm and falls back to RS256,
while providers are free to sign with something else — ES256, in the case this
was first tested against. See :class:`SigningAlgorithmVerifier`.

One consequence worth stating plainly: embedding the id token in the FastMCP JWT
means the client holds it too. That is a real widening, though a narrow one — the
client just proved it controls that identity, so it is not learning a secret
about anyone else. Keeping the token server-side instead would mean reading
FastMCP's private token stores on every call, which is a worse trade for a
component this load-bearing.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from typing import Any

from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier
from mcp.server.auth.provider import AccessToken

from gsf_mcp.config import Settings

logger = logging.getLogger(__name__)

# Namespaced because it rides inside the JWT next to provider claims, where a
# bare `id_token` risks colliding with something the provider already sends.
ID_TOKEN_CLAIM = "gsf_id_token"


def _unverified_algorithm(token: str) -> str | None:
    """The ``alg`` a JWT claims, read without trusting anything in it."""
    header, _, _ = token.partition(".")
    if not header:
        return None
    try:
        # No padding is carried in the compact form; add the most it could need.
        decoded = base64.urlsafe_b64decode(header + "==")
        alg = json.loads(decoded).get("alg")
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    return alg if isinstance(alg, str) else None


class SigningAlgorithmVerifier(TokenVerifier):
    """Verify a token under whichever algorithm its issuer actually used.

    :class:`JWTVerifier` is fixed to a single algorithm and falls back to RS256,
    so a provider that signs with anything else has every token rejected. That
    surfaces as a bare 401 after a sign-in that went perfectly, which is a
    miserable thing to debug — and it is avoidable, because the provider
    advertises the algorithms it may use and each token names the one it was
    signed with. Neither has to be guessed at or configured by hand.

    Verifying only against the advertised set is the point of the allow-list: it
    refuses a token naming an algorithm this issuer would never sign with,
    rather than trying whatever it asks for.
    """

    def __init__(
        self,
        *,
        jwks_uri: str,
        issuer: str,
        audience: str | None,
        algorithms: tuple[str, ...],
    ) -> None:
        super().__init__()
        self._verifiers = {
            algorithm: JWTVerifier(
                jwks_uri=jwks_uri,
                issuer=issuer,
                audience=audience,
                algorithm=algorithm,
            )
            for algorithm in algorithms
        }
        logger.debug("Accepting id tokens signed with %s", ", ".join(algorithms))

    async def verify_token(self, token: str) -> AccessToken | None:
        algorithm = _unverified_algorithm(token)
        verifier = self._verifiers.get(algorithm or "")
        if verifier is None:
            logger.debug(
                "Rejecting a token signed with %r; this issuer advertises %s",
                algorithm,
                ", ".join(self._verifiers) or "nothing",
            )
            return None
        return await verifier.verify_token(token)


class GsfOidcProxy(OIDCProxy):
    """An :class:`OIDCProxy` that keeps hold of the upstream id token."""

    def get_token_verifier(
        self,
        *,
        algorithm: str | None = None,
        audience: str | None = None,
        required_scopes: list[str] | None = None,
        timeout_seconds: int | None = None,
    ) -> TokenVerifier:
        """Verify id tokens against every algorithm the provider advertises.

        An explicit ``algorithm`` still wins, for a provider whose advertised
        list cannot be trusted.
        """
        advertised = tuple(self.oidc_config.id_token_signing_alg_values_supported or ())
        algorithms = (algorithm,) if algorithm else advertised
        if not algorithms:
            # Nothing to build an allow-list from, so defer to FastMCP rather
            # than invent one.
            return super().get_token_verifier(
                algorithm=algorithm,
                audience=audience,
                required_scopes=required_scopes,
                timeout_seconds=timeout_seconds,
            )

        return SigningAlgorithmVerifier(
            jwks_uri=str(self.oidc_config.jwks_uri),
            issuer=str(self.oidc_config.issuer),
            audience=audience,
            algorithms=algorithms,
        )

    async def _extract_upstream_claims(
        self, idp_tokens: dict[str, Any]
    ) -> dict[str, Any] | None:
        claims = dict(await super()._extract_upstream_claims(idp_tokens) or {})

        id_token = (idp_tokens.get("id_token") or "").strip()
        if not id_token:
            # Means the provider returned no id token at all, so every GSF call
            # from this session will fail to authenticate. Worth a loud line:
            # the sign-in itself will have looked completely successful.
            logger.error(
                "Provider returned no id_token, so there is nothing to "
                "authenticate GSF calls with. Check that the 'openid' scope is "
                "granted for client %s.",
                self._upstream_client_id,
            )
            return claims or None

        claims[ID_TOKEN_CLAIM] = id_token
        return claims


def build_auth(settings: Settings) -> GsfOidcProxy | None:
    """Build the auth provider, or ``None`` when sign-in was not configured."""
    oidc = settings.oidc
    if oidc is None:
        return None

    return GsfOidcProxy(
        config_url=oidc.config_url,
        client_id=oidc.client_id,
        client_secret=oidc.client_secret,
        base_url=oidc.public_url,
        # Verify and expose the id token rather than the access token, for the
        # reasons in the module docstring.
        verify_id_token=True,
        # These reach the provider's authorize call, so omitting `openid` would
        # mean no id token comes back at all. `load_settings` refuses that.
        required_scopes=list(oidc.scopes),
        redirect_path=oidc.redirect_path,
    )


def sso_id_token() -> str | None:
    """The signed-in user's id token for this request, if there is one.

    Returns ``None`` whenever the request did not arrive through OIDC sign-in,
    which includes every request under stdio.
    """

    # Imported here because it resolves request state: at module scope it would
    # bind at import time, outside any request.
    from fastmcp.server.dependencies import get_access_token

    try:
        token = get_access_token()
    except Exception:
        # Raised when there is no authenticated request in scope, which is a
        # normal state rather than a fault.
        return None

    claims = getattr(token, "claims", None) or {}
    upstream = claims.get("upstream_claims")
    if not isinstance(upstream, dict):
        return None

    value = upstream.get(ID_TOKEN_CLAIM)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


__all__ = [
    "ID_TOKEN_CLAIM",
    "GsfOidcProxy",
    "SigningAlgorithmVerifier",
    "build_auth",
    "sso_id_token",
]
