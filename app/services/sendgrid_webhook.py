from __future__ import annotations

import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


def verify_sendgrid_signature(
    payload: bytes,
    timestamp: str | None,
    signature: str | None,
    public_key: str | None,
) -> bool:
    if not timestamp or not signature or not public_key:
        return False
    try:
        key = serialization.load_der_public_key(base64.b64decode(public_key))
        if not isinstance(key, ec.EllipticCurvePublicKey):
            return False
        key.verify(
            base64.b64decode(signature),
            timestamp.encode("utf-8") + payload,
            ec.ECDSA(hashes.SHA256()),
        )
        return True
    except (ValueError, TypeError, InvalidSignature):
        return False

