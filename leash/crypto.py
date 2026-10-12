"""Ed25519 keys and compact JWS (RFC 7515, alg "EdDSA" per RFC 8037), plus canonical hashing.

Mandates are signed by the user's key; intent receipts are signed by Leash's receipt key, whose public half is
published at /.well-known/jwks.json so merchants can verify receipts offline.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


class SignatureError(ValueError):
    pass


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def canonical(obj: Any) -> bytes:
    """Deterministic JSON: sorted keys, no whitespace, Decimals and datetimes as strings."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()


def sha256_hex(obj: Any) -> str:
    data = obj if isinstance(obj, (bytes, bytearray)) else canonical(obj)
    return hashlib.sha256(data).hexdigest()


@dataclass
class KeyPair:
    kid: str
    private: Ed25519PrivateKey

    @property
    def public(self) -> Ed25519PublicKey:
        return self.private.public_key()

    def public_jwk(self) -> dict[str, str]:
        raw = self.public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return {"kty": "OKP", "crv": "Ed25519", "x": b64u(raw), "kid": self.kid, "use": "sig", "alg": "EdDSA"}

    @classmethod
    def generate(cls, kid: str) -> "KeyPair":
        return cls(kid, Ed25519PrivateKey.generate())

    @classmethod
    def load_or_create(cls, path: Path, kid: str) -> "KeyPair":
        if path.exists():
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
            assert isinstance(key, Ed25519PrivateKey)
            return cls(kid, key)
        kp = cls.generate(kid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            kp.private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        )
        return kp


def public_key_from_jwk(jwk: dict[str, Any]) -> Ed25519PublicKey:
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519" or not jwk.get("x"):
        raise SignatureError("only Ed25519 OKP keys are supported")
    raw = b64u_decode(jwk["x"])
    if len(raw) != 32:
        raise SignatureError("bad Ed25519 public key length")
    return Ed25519PublicKey.from_public_bytes(raw)


def jws_sign(payload: dict[str, Any], key: KeyPair, typ: str) -> str:
    header = {"alg": "EdDSA", "typ": typ, "kid": key.kid}
    signing_input = b64u(canonical(header)) + "." + b64u(canonical(payload))
    sig = key.private.sign(signing_input.encode())
    return signing_input + "." + b64u(sig)


def jws_parts(token: str) -> tuple[dict, dict, bytes, str]:
    try:
        h, p, s = token.split(".")
        header = json.loads(b64u_decode(h))
        payload = json.loads(b64u_decode(p))
        return header, payload, b64u_decode(s), f"{h}.{p}"
    except (ValueError, json.JSONDecodeError) as e:
        raise SignatureError(f"malformed JWS: {e}") from e


def jws_peek(token: str) -> tuple[dict, dict]:
    """Decode without verifying. Never trust the result for authorisation."""
    header, payload, _, _ = jws_parts(token)
    return header, payload


def jws_verify(token: str, public: Ed25519PublicKey | dict, expected_typ: str | None = None) -> tuple[dict, dict]:
    header, payload, sig, signing_input = jws_parts(token)
    if header.get("alg") != "EdDSA":
        raise SignatureError(f"unexpected alg {header.get('alg')!r}")  # blocks alg=none and alg confusion
    if expected_typ and header.get("typ") != expected_typ:
        raise SignatureError(f"unexpected typ {header.get('typ')!r}")
    pub = public_key_from_jwk(public) if isinstance(public, dict) else public
    try:
        pub.verify(sig, signing_input.encode())
    except InvalidSignature as e:
        raise SignatureError("signature does not verify") from e
    return header, payload
