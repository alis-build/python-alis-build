"""The issuer's signing keys, and verifying a token against them."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .._errors import InvalidTokenError, SignatureVerificationError
from .._wire import b64url_decode
from ._protocol import JwtParts

_logger: Final = logging.getLogger(__name__)

SUPPORTED_ALGORITHMS: Final = frozenset({"RS256"})
"""Signature algorithms this package will verify.

Restricted on purpose. An issuer that starts signing with something else
should be a loud failure rather than a token that quietly goes unchecked.
"""

DEFAULT_KEY_TTL: Final = timedelta(hours=1)
"""How long a fetched key set is trusted before being refetched."""

DEFAULT_MIN_REFRESH_INTERVAL: Final = timedelta(seconds=30)
"""The shortest gap between two fetches of the key set.

Without this, a token naming a key id the issuer has never published would
send one request to the issuer for every request that arrives.
"""


@dataclass(frozen=True, slots=True)
class SigningKey:
    """One public key published by the issuer."""

    kid: str
    public_key: rsa.RSAPublicKey
    algorithm: str = "RS256"


def require_algorithm(header: dict[str, Any], /) -> str:
    """Reads the signing algorithm from a token header and checks it.

    Raises:
        InvalidTokenError: If the header names no algorithm, or names one this
            package will not verify.
    """
    algorithm = header.get("alg")
    if not isinstance(algorithm, str) or not algorithm:
        raise InvalidTokenError("the token header names no signing algorithm")
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise InvalidTokenError(
            f"the token is signed with {algorithm!r}; "
            f"only {sorted(SUPPORTED_ALGORITHMS)} are verified"
        )
    return algorithm


def require_key_id(header: dict[str, Any], /) -> str:
    """Reads the key id from a token header.

    Raises:
        InvalidTokenError: If the header carries no usable key id.
    """
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise InvalidTokenError("the token header names no key id")
    return kid


def _big_int(value: str, what: str) -> int:
    """Decodes a base64url-encoded big-endian integer from a key."""
    try:
        return int.from_bytes(b64url_decode(value), "big")
    except Exception as exc:
        raise ValueError(f"could not decode the key {what}: {exc}") from exc


def parse_jwks(body: bytes, /) -> dict[str, SigningKey]:
    """Reads the issuer's published key set.

    Both the standard ``n``/``e`` field names and the older ``mod``/``exp``
    spelling are accepted, since issuers are not all on the same version. A key
    that cannot be read is skipped rather than failing the whole set, so one
    malformed entry cannot lock everyone out.

    Raises:
        ValueError: If the document is not a key set at all.
    """
    try:
        document = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"the key set is not JSON: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
        raise ValueError("the key set has no 'keys' array")

    keys: dict[str, SigningKey] = {}
    for entry in document["keys"]:
        if not isinstance(entry, dict):
            continue
        kid = entry.get("kid")
        if not isinstance(kid, str) or not kid:
            _logger.debug("skipping a published key with no key id")
            continue
        key_type = entry.get("kty")
        if key_type is not None and key_type != "RSA":
            _logger.debug("skipping key %r: %r keys are not verified", kid, key_type)
            continue

        modulus = entry.get("n") or entry.get("mod")
        exponent = entry.get("e") or entry.get("exp")
        if not isinstance(modulus, str) or not isinstance(exponent, str):
            _logger.debug("skipping key %r: no usable modulus and exponent", kid)
            continue
        try:
            public = rsa.RSAPublicNumbers(
                e=_big_int(exponent, "exponent"), n=_big_int(modulus, "modulus")
            ).public_key()
        except ValueError as exc:
            _logger.debug("skipping key %r: %s", kid, exc)
            continue
        keys[kid] = SigningKey(kid=kid, public_key=public)

    return keys


def verify_signature(parts: JwtParts, key: SigningKey, /) -> None:
    """Checks a token's signature against a published key.

    Raises:
        SignatureVerificationError: If the signature does not verify, or is not
            decodable.
    """
    try:
        signature = b64url_decode(parts.signature)
    except Exception as exc:
        raise SignatureVerificationError(f"could not decode the signature: {exc}") from exc
    if not signature:
        raise SignatureVerificationError("the token carries no signature")

    try:
        key.public_key.verify(signature, parts.signing_input, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise SignatureVerificationError(
            f"the signature does not verify against key {key.kid!r}"
        ) from exc


class KeyStore:
    """Caches the issuer's published keys.

    The store never fetches anything itself; it decides *whether* a fetch is
    worth making, and holds what comes back. That keeps the decision in one
    place for both the async and blocking clients.

    Args:
        ttl: How long a fetched key set is trusted.
        min_refresh_interval: The shortest gap between two fetches, so a token
            naming a key the issuer has never published cannot turn every
            request into a request to the issuer.
    """

    __slots__ = ("_fetched_at", "_keys", "_last_attempt", "_lock", "_min_interval", "_ttl")

    def __init__(
        self,
        *,
        ttl: timedelta = DEFAULT_KEY_TTL,
        min_refresh_interval: timedelta = DEFAULT_MIN_REFRESH_INTERVAL,
    ) -> None:
        self._keys: dict[str, SigningKey] = {}
        self._fetched_at: datetime | None = None
        self._last_attempt: datetime | None = None
        self._ttl = ttl
        self._min_interval = min_refresh_interval
        self._lock = threading.Lock()

    def get(self, kid: str, /, *, now: datetime | None = None) -> SigningKey | None:
        """Returns a cached key, or ``None`` if it is absent or has aged out."""
        if self._is_stale(now or datetime.now(tz=UTC)):
            return None
        return self._keys.get(kid)

    def _is_stale(self, now: datetime) -> bool:
        return self._fetched_at is None or self._fetched_at + self._ttl <= now

    def should_fetch(self, kid: str, /, *, now: datetime | None = None) -> bool:
        """Reports whether it is worth fetching the key set again.

        Returns ``True`` when the wanted key is missing or the set has aged
        out, unless a fetch was attempted too recently to try again.
        """
        moment = now or datetime.now(tz=UTC)
        if not self._is_stale(moment) and kid in self._keys:
            return False
        # A recent attempt, successful or not, holds off the next one.
        return self._last_attempt is None or self._last_attempt + self._min_interval <= moment

    def record_attempt(self, *, now: datetime | None = None) -> None:
        """Notes that a fetch was tried, whether or not it worked."""
        with self._lock:
            self._last_attempt = now or datetime.now(tz=UTC)

    def replace(self, keys: dict[str, SigningKey], /, *, now: datetime | None = None) -> None:
        """Installs a freshly fetched key set."""
        moment = now or datetime.now(tz=UTC)
        with self._lock:
            self._keys = dict(keys)
            self._fetched_at = moment
            self._last_attempt = moment

    def clear(self) -> None:
        """Discards everything, so the next lookup fetches."""
        with self._lock:
            self._keys = {}
            self._fetched_at = None
            self._last_attempt = None

    @property
    def key_ids(self) -> frozenset[str]:
        """The key ids currently held."""
        return frozenset(self._keys)
