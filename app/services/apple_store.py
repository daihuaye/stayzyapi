from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings


class AppleVerificationUnavailable(RuntimeError):
    pass


class AppleVerificationFailed(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedStoreTransaction:
    transaction_id: str
    original_transaction_id: str
    product_id: str
    app_account_token: str | None
    environment: str
    purchased_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None

    @property
    def status(self) -> str:
        if self.revoked_at is not None:
            return "revoked"
        if self.expires_at is not None and self.expires_at <= datetime.now(UTC):
            return "expired"
        return "active"


@dataclass(frozen=True)
class VerifiedNotification:
    notification_id: str
    notification_type: str
    subtype: str | None
    transaction: VerifiedStoreTransaction | None
    grace_period_expires_at: datetime | None = None
    is_in_billing_retry_period: bool | None = None


def _milliseconds(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(int(value) / 1000, UTC)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    return str(raw)


class AppleStoreVerifier:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._verifier: Any | None = None

    def _build_verifier(self) -> Any:
        if self._verifier is not None:
            return self._verifier
        if not self.settings.apple_root_certificate_paths:
            raise AppleVerificationUnavailable("Apple root certificates are not configured")
        try:
            from appstoreserverlibrary.models.Environment import Environment
            from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier

            environment = (
                Environment.PRODUCTION
                if self.settings.apple_environment == "Production"
                else Environment.SANDBOX
            )
            roots = [Path(path).read_bytes() for path in self.settings.apple_root_certificate_paths]
            self._verifier = SignedDataVerifier(
                roots,
                True,
                environment,
                self.settings.apple_bundle_id,
                self.settings.apple_app_id,
            )
            return self._verifier
        except AppleVerificationUnavailable:
            raise
        except Exception as error:
            raise AppleVerificationUnavailable("Apple verifier could not be initialized") from error

    async def verify_transaction(self, signed_transaction: str) -> VerifiedStoreTransaction:
        verifier = self._build_verifier()
        try:
            decoded = await asyncio.to_thread(
                verifier.verify_and_decode_signed_transaction,
                signed_transaction,
            )
            return self._transaction(decoded)
        except AppleVerificationUnavailable:
            raise
        except Exception as error:
            status = getattr(error, "status", None)
            if getattr(status, "name", "") == "RETRYABLE_VERIFICATION_FAILURE":
                raise AppleVerificationUnavailable("Apple verification is temporarily unavailable") from error
            raise AppleVerificationFailed("Apple transaction verification failed") from error

    async def verify_notification(self, signed_payload: str) -> VerifiedNotification:
        verifier = self._build_verifier()
        try:
            decoded = await asyncio.to_thread(verifier.verify_and_decode_notification, signed_payload)
            transaction = None
            data = getattr(decoded, "data", None)
            signed_transaction = getattr(data, "signedTransactionInfo", None) if data else None
            if signed_transaction:
                transaction = await self.verify_transaction(signed_transaction)
            grace_period_expires_at = None
            is_in_billing_retry_period = None
            signed_renewal = getattr(data, "signedRenewalInfo", None) if data else None
            if signed_renewal:
                renewal = await asyncio.to_thread(
                    verifier.verify_and_decode_renewal_info,
                    signed_renewal,
                )
                grace_period_expires_at = _milliseconds(
                    getattr(renewal, "gracePeriodExpiresDate", None)
                )
                is_in_billing_retry_period = getattr(
                    renewal,
                    "isInBillingRetryPeriod",
                    None,
                )
            notification_id = _text(getattr(decoded, "notificationUUID", None))
            if not notification_id:
                notification_id = hashlib.sha256(signed_payload.encode("utf-8")).hexdigest()
            return VerifiedNotification(
                notification_id=notification_id,
                notification_type=_text(getattr(decoded, "notificationType", None)) or "UNKNOWN",
                subtype=_text(getattr(decoded, "subtype", None)),
                transaction=transaction,
                grace_period_expires_at=grace_period_expires_at,
                is_in_billing_retry_period=is_in_billing_retry_period,
            )
        except (AppleVerificationUnavailable, AppleVerificationFailed):
            raise
        except Exception as error:
            status = getattr(error, "status", None)
            if getattr(status, "name", "") == "RETRYABLE_VERIFICATION_FAILURE":
                raise AppleVerificationUnavailable("Apple verification is temporarily unavailable") from error
            raise AppleVerificationFailed("Apple notification verification failed") from error

    def _transaction(self, decoded: Any) -> VerifiedStoreTransaction:
        transaction_id = _text(getattr(decoded, "transactionId", None))
        original_id = _text(getattr(decoded, "originalTransactionId", None))
        product_id = _text(getattr(decoded, "productId", None))
        purchased_at = _milliseconds(getattr(decoded, "purchaseDate", None))
        if not transaction_id or not original_id or not product_id or not purchased_at:
            raise AppleVerificationFailed("Apple transaction is missing required claims")
        return VerifiedStoreTransaction(
            transaction_id=transaction_id,
            original_transaction_id=original_id,
            product_id=product_id,
            app_account_token=_text(getattr(decoded, "appAccountToken", None)),
            environment=_text(getattr(decoded, "environment", None)) or self.settings.apple_environment,
            purchased_at=purchased_at,
            expires_at=_milliseconds(getattr(decoded, "expiresDate", None)),
            revoked_at=_milliseconds(getattr(decoded, "revocationDate", None)),
        )
