"""Zero-Knowledge Privacy Vault.

Provides deterministic in-line pseudonymization for PII/PHI (SSN, credit card,
email, phone, named entities), ensuring sensitive raw values never reach external
LLM providers or unencrypted logs, with authorized cryptographic re-hydration.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from typing import Any
from uuid import UUID

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
except ImportError:
    Fernet = None  # type: ignore[misc, assignment]
    HKDF = None  # type: ignore[misc, assignment]
    hashes = None  # type: ignore[misc, assignment]

from knowledgeforge.config import get_settings

logger = logging.getLogger(__name__)

# Core PII Detection Patterns
_EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SSN_REGEX = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE_REGEX = re.compile(r"\b(?:\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b")
_CARD_REGEX = re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b")
_IBAN_REGEX = re.compile(r"\b[A-Z]{2}\d{2}[A-Za-z0-9]{11,30}\b")
_TITLE_NAME_REGEX = re.compile(
    r"\b(?:Mr\.|Mrs\.|Ms\.|Dr\.|Prof\.)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b"
)


@dataclass
class MaskedEntity:
    raw_value: str
    entity_type: str
    surrogate_token: str
    start: int
    end: int


class PrivacyVault:
    """Cryptographic PII/PHI vault and deterministic pseudonymizer."""

    def __init__(self, master_secret: str | None = None) -> None:
        settings = get_settings()
        self.master_secret = (
            master_secret
            or getattr(settings, "vault_master_key", "")
            or "knowledgeforge-dev-vault-master-key-32chars"
        )

    def _get_cipher(self, tenant_id: UUID, salt: bytes | None = None) -> Any:
        """Derive a tenant-specific Fernet cipher using HKDF when salt is provided."""
        if Fernet is None or HKDF is None or hashes is None:
            raise RuntimeError(
                "Cryptography package (Fernet/HKDF) is required for PrivacyVault operations"
            )
        if salt is not None:
            hkdf = HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                info=f"tenant:{tenant_id}".encode(),
            )
            key_material = hkdf.derive(self.master_secret.encode())
        else:
            # Legacy deterministic derivation fallback
            key_material = hashlib.sha256(f"{tenant_id}:{self.master_secret}".encode()).digest()
        url_safe_key = base64.urlsafe_b64encode(key_material)
        return Fernet(url_safe_key)

    def encrypt_value(self, raw_value: str, tenant_id: UUID) -> str:
        """Encrypt a raw PII string for tenant storage using random per-record salt."""
        salt = secrets.token_bytes(16)
        cipher = self._get_cipher(tenant_id, salt=salt)
        ciphertext = cipher.encrypt(raw_value.encode("utf-8")).decode("utf-8")
        salt_b64 = base64.urlsafe_b64encode(salt).decode("utf-8")
        return f"{salt_b64}:{ciphertext}"

    def decrypt_value(self, encrypted_value: str, tenant_id: UUID) -> str:
        """Decrypt an encrypted PII string. Fails closed on insecure obfuscation."""
        if encrypted_value.startswith("vault_obf:"):
            raise RuntimeError(
                "Insecure base64 obfuscation detected in vault ciphertext; fail-closed refusing to decrypt"
            )
        if ":" in encrypted_value:
            salt_b64, ciphertext = encrypted_value.split(":", 1)
            salt = base64.urlsafe_b64decode(salt_b64.encode("utf-8"))
            cipher = self._get_cipher(tenant_id, salt=salt)
            return cipher.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        # Legacy format without salt
        cipher = self._get_cipher(tenant_id)
        return cipher.decrypt(encrypted_value.encode("utf-8")).decode("utf-8")

    def generate_surrogate_token(self, entity_type: str, raw_value: str, tenant_id: UUID) -> str:
        """Generate a deterministic surrogate token scoped to the tenant.

        Identical raw values within a tenant yield identical surrogate tokens,
        preserving entity co-reference in documents and LLM answers.
        """
        digest = hmac.new(
            str(tenant_id).encode(), raw_value.strip().encode(), hashlib.sha256
        ).hexdigest()[:8]
        return f"[{entity_type.upper()}_{digest}]"

    def detect_entities(self, text: str, tenant_id: UUID) -> list[MaskedEntity]:
        """Detect sensitive PII/PHI spans across the input text."""
        entities: list[MaskedEntity] = []

        detectors = [
            ("EMAIL", _EMAIL_REGEX),
            ("SSN", _SSN_REGEX),
            ("CARD", _CARD_REGEX),
            ("PHONE", _PHONE_REGEX),
            ("IBAN", _IBAN_REGEX),
            ("PERSON", _TITLE_NAME_REGEX),
        ]

        for entity_type, pattern in detectors:
            for match in pattern.finditer(text):
                raw = match.group(0)
                # Extra validation for phone: avoid pure 4-digit or standard years
                if entity_type == "PHONE" and not any(c in raw for c in ("-", "(", ")", "+", " ")):
                    continue
                token = self.generate_surrogate_token(entity_type, raw, tenant_id)
                entities.append(
                    MaskedEntity(
                        raw_value=raw,
                        entity_type=entity_type,
                        surrogate_token=token,
                        start=match.start(),
                        end=match.end(),
                    )
                )

        # Sort by start position descending to allow clean non-overlapping replacement
        entities.sort(key=lambda e: e.start, reverse=True)
        return entities

    def mask_text(
        self,
        text: str,
        tenant_id: UUID,
        conn: Any = None,
        store: bool = True,
    ) -> tuple[str, dict[str, str]]:
        """Replace all PII spans with surrogate tokens and record mappings."""
        entities = self.detect_entities(text, tenant_id)
        if not entities:
            return text, {}

        # Filter out overlapping entities (prefer earlier detected or larger span)
        filtered_entities: list[MaskedEntity] = []
        occupied_spans: list[tuple[int, int]] = []

        for ent in entities:
            overlaps = any(
                not (ent.end <= start or ent.start >= end) for start, end in occupied_spans
            )
            if not overlaps:
                filtered_entities.append(ent)
                occupied_spans.append((ent.start, ent.end))

        # Re-sort descending by start offset
        filtered_entities.sort(key=lambda e: e.start, reverse=True)

        token_to_raw: dict[str, str] = {}
        to_persist: list[tuple[str, str, str]] = []

        masked_text = text
        for ent in filtered_entities:
            token_to_raw[ent.surrogate_token] = ent.raw_value
            to_persist.append((ent.surrogate_token, ent.entity_type, ent.raw_value))
            masked_text = masked_text[: ent.start] + ent.surrogate_token + masked_text[ent.end :]

        if store and conn is not None and to_persist:
            self.store_surrogates(conn, tenant_id, to_persist)

        return masked_text, token_to_raw

    def unmask_text(
        self,
        text: str,
        tenant_id: UUID,
        conn: Any = None,
        memory_map: dict[str, str] | None = None,
    ) -> str:
        """Re-hydrate surrogate tokens back into original plaintext values."""
        unmasked = text
        # 1. Use memory map first if provided
        if memory_map:
            for token, raw in memory_map.items():
                unmasked = unmasked.replace(token, raw)

        # 2. Check for remaining surrogate patterns in text: [TYPE_hash]
        remaining_tokens = re.findall(r"\[[A-Z]+_[a-f0-9]{8}\]", unmasked)
        if remaining_tokens and conn is not None:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT surrogate_token, encrypted_value
                    FROM pseudonym_vault
                    WHERE tenant_id = %s AND surrogate_token = ANY(%s);
                    """,
                    (tenant_id, list(set(remaining_tokens))),
                )
                rows = cur.fetchall()
                for token, enc_val in rows:
                    try:
                        raw = self.decrypt_value(enc_val, tenant_id)
                        unmasked = unmasked.replace(token, raw)
                    except Exception as exc:
                        logger.warning("Failed to decrypt token %s: %s", token, exc)

        return unmasked

    def store_surrogates(
        self,
        conn: Any,
        tenant_id: UUID,
        records: list[tuple[str, str, str]],  # (surrogate_token, entity_type, raw_value)
    ) -> None:
        """Persist encrypted mappings into pseudonym_vault."""
        with conn.cursor() as cur:
            for surrogate_token, entity_type, raw_value in records:
                enc_value = self.encrypt_value(raw_value, tenant_id)
                cur.execute(
                    """
                    INSERT INTO pseudonym_vault (tenant_id, surrogate_token, entity_type, encrypted_value)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (tenant_id, surrogate_token) DO NOTHING;
                    """,
                    (tenant_id, surrogate_token, entity_type, enc_value),
                )

    def list_vault_records(
        self,
        conn: Any,
        tenant_id: UUID,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List pseudonym tokens and entity classifications for audit."""
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, surrogate_token, entity_type, created_at
                FROM pseudonym_vault
                WHERE tenant_id = %s
                ORDER BY created_at DESC
                LIMIT %s;
                """,
                (tenant_id, limit),
            )
            rows = cur.fetchall()
            return [
                {
                    "id": str(r[0]),
                    "surrogate_token": r[1],
                    "entity_type": r[2],
                    "created_at": r[3].isoformat() if r[3] else None,
                }
                for r in rows
            ]
