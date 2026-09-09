"""Unit tests for Feature 10: Zero-Knowledge Privacy Vault."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from knowledgeforge.security.privacy_vault import PrivacyVault


def test_detect_and_mask_pii_entities() -> None:
    vault = PrivacyVault()
    tenant_id = uuid4()
    raw_text = (
        "Please contact Dr. Sarah Connor at sarah.connor@cyberdyne.com or 415-555-0199. "
        "Her SSN is 123-45-6789 and Card is 4532-1234-5678-9012."
    )

    masked_text, token_map = vault.mask_text(raw_text, tenant_id=tenant_id, store=False)

    assert "sarah.connor@cyberdyne.com" not in masked_text
    assert "123-45-6789" not in masked_text
    assert "4532-1234-5678-9012" not in masked_text
    assert "[EMAIL_" in masked_text
    assert "[SSN_" in masked_text
    assert "[CARD_" in masked_text
    assert "[PHONE_" in masked_text

    # Verify all tokens map back to the original values
    assert len(token_map) >= 4


def test_deterministic_surrogate_tokens() -> None:
    vault = PrivacyVault()
    tenant_id = uuid4()
    email = "ceo@globalcorp.com"

    token1 = vault.generate_surrogate_token("EMAIL", email, tenant_id)
    token2 = vault.generate_surrogate_token("EMAIL", email, tenant_id)
    assert token1 == token2


def test_cross_tenant_token_isolation() -> None:
    vault = PrivacyVault()
    tenant_a = uuid4()
    tenant_b = uuid4()
    ssn = "987-65-4321"

    token_a = vault.generate_surrogate_token("SSN", ssn, tenant_a)
    token_b = vault.generate_surrogate_token("SSN", ssn, tenant_b)
    assert token_a != token_b


def test_encryption_and_decryption() -> None:
    vault = PrivacyVault()
    tenant_id = uuid4()
    secret_value = "SuperSecretContractValue#123"

    encrypted = vault.encrypt_value(secret_value, tenant_id)
    assert encrypted != secret_value
    assert ":" in encrypted  # salt:ciphertext format

    decrypted = vault.decrypt_value(encrypted, tenant_id)
    assert decrypted == secret_value


def test_encryption_random_salt_per_record_and_tenant_isolation() -> None:
    vault = PrivacyVault()
    tenant_a = uuid4()
    tenant_b = uuid4()
    secret = "ConfidentialFormulaX"

    enc_a1 = vault.encrypt_value(secret, tenant_a)
    enc_a2 = vault.encrypt_value(secret, tenant_a)
    enc_b = vault.encrypt_value(secret, tenant_b)

    # Different salts per record
    assert enc_a1 != enc_a2
    assert enc_a1.split(":")[0] != enc_a2.split(":")[0]
    assert enc_a1.split(":")[0] != enc_b.split(":")[0]

    # Decryption works with correct tenant
    assert vault.decrypt_value(enc_a1, tenant_a) == secret
    assert vault.decrypt_value(enc_b, tenant_b) == secret

    # Attempting to decrypt tenant A's ciphertext with tenant B's context raises InvalidToken
    from cryptography.fernet import InvalidToken
    with pytest.raises(InvalidToken):
        vault.decrypt_value(enc_a1, tenant_b)


def test_missing_cryptography_fails_closed(monkeypatch) -> None:
    from knowledgeforge.security import privacy_vault
    monkeypatch.setattr(privacy_vault, "Fernet", None)
    vault = PrivacyVault()
    tenant_id = uuid4()

    with pytest.raises(RuntimeError, match="Cryptography package .* is required"):
        vault.encrypt_value("secret", tenant_id)


def test_insecure_base64_obfuscation_fails_closed() -> None:
    vault = PrivacyVault()
    tenant_id = uuid4()

    with pytest.raises(RuntimeError, match="Insecure base64 obfuscation detected"):
        vault.decrypt_value("vault_obf:c2VjcmV0", tenant_id)



def test_unmask_text_roundtrip() -> None:
    vault = PrivacyVault()
    tenant_id = uuid4()
    raw_text = "Send an invoice to john.doe@acme.org regarding 555-123-4567."

    masked, token_map = vault.mask_text(raw_text, tenant_id=tenant_id, store=False)
    unmasked = vault.unmask_text(masked, tenant_id=tenant_id, memory_map=token_map)

    assert unmasked == raw_text


def test_vault_db_storage_and_unmask() -> None:
    vault = PrivacyVault()
    tenant_id = uuid4()
    mock_conn = MagicMock()

    records = [("[EMAIL_12345678]", "EMAIL", "alice@example.com")]
    vault.store_surrogates(mock_conn, tenant_id, records)
    mock_conn.cursor.return_value.__enter__.return_value.execute.assert_called()

    # Test DB-based unmasking
    enc_value = vault.encrypt_value("alice@example.com", tenant_id)
    mock_cur = mock_conn.cursor.return_value.__enter__.return_value
    mock_cur.fetchall.return_value = [("[EMAIL_12345678]", enc_value)]

    text_with_token = "Report submitted by [EMAIL_12345678] today."
    unmasked = vault.unmask_text(text_with_token, tenant_id=tenant_id, conn=mock_conn)

    assert "alice@example.com" in unmasked
    assert "[EMAIL_12345678]" not in unmasked


def test_unmask_endpoint_requires_owner_and_logs_audit(monkeypatch) -> None:
    from contextlib import nullcontext
    from fastapi.testclient import TestClient
    from knowledgeforge import api
    from knowledgeforge.main import app

    user_id = uuid4()
    tenant_id = uuid4()

    # 1. Member gets 403
    app.dependency_overrides[api.require_owner] = lambda: (user_id, tenant_id, "member", False)
    # The dependency checker itself raises 403 if role is not owner
    from fastapi import HTTPException
    def member_checker():
        raise HTTPException(status_code=403, detail="Requires one of roles: owner")
    app.dependency_overrides[api.require_owner] = member_checker

    client = TestClient(app)
    resp = client.post("/privacy/unmask", json={"masked_text": "Hello [EMAIL_12345678]"})
    assert resp.status_code == 403

    # 2. Owner succeeds and writes audit log
    app.dependency_overrides[api.require_owner] = lambda: (user_id, tenant_id, "owner", False)

    audit_logs_recorded = []
    def fake_record_audit(connection, *, tenant_id, user_id, action, details=None):
        audit_logs_recorded.append({
            "tenant_id": tenant_id,
            "user_id": user_id,
            "action": action,
            "details": details,
        })

    monkeypatch.setattr(api, "record_audit_log", fake_record_audit)
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(MagicMock()))

    resp_owner = client.post("/privacy/unmask", json={"masked_text": "Hello [EMAIL_12345678]"})
    assert resp_owner.status_code == 200
    assert len(audit_logs_recorded) == 1
    assert audit_logs_recorded[0]["action"] == "privacy.unmask"
    assert audit_logs_recorded[0]["user_id"] == user_id
    assert audit_logs_recorded[0]["tenant_id"] == tenant_id
    assert audit_logs_recorded[0]["details"]["surrogate_tokens"] == ["[EMAIL_12345678]"]
    assert audit_logs_recorded[0]["details"]["tokens_count"] == 1

    app.dependency_overrides.clear()


def test_list_vault_endpoint_requires_owner(monkeypatch) -> None:
    from contextlib import nullcontext
    from fastapi import HTTPException
    from fastapi.testclient import TestClient
    from knowledgeforge import api
    from knowledgeforge.main import app

    user_id = uuid4()
    tenant_id = uuid4()

    def member_checker():
        raise HTTPException(status_code=403, detail="Requires one of roles: owner")
    app.dependency_overrides[api.require_owner] = member_checker

    client = TestClient(app)
    resp = client.get("/privacy/vault")
    assert resp.status_code == 403

    # Owner succeeds
    app.dependency_overrides[api.require_owner] = lambda: (user_id, tenant_id, "owner", False)
    mock_conn = MagicMock()
    mock_cur = mock_conn.cursor.return_value.__enter__.return_value
    mock_cur.fetchall.return_value = []
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(mock_conn))

    resp_owner = client.get("/privacy/vault")
    assert resp_owner.status_code == 200
    assert resp_owner.json() == []

    app.dependency_overrides.clear()


def test_mask_endpoint_allows_member(monkeypatch) -> None:
    from contextlib import nullcontext
    from fastapi.testclient import TestClient
    from knowledgeforge import api
    from knowledgeforge.main import app

    user_id = uuid4()
    tenant_id = uuid4()

    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(MagicMock()))
    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "member", False)
    client = TestClient(app)
    resp = client.post("/privacy/mask", json={"text": "Contact john@example.com", "store_in_vault": False})
    assert resp.status_code == 200
    assert "[EMAIL_" in resp.json()["masked_text"]
    assert "john@example.com" not in resp.json()["masked_text"]

    app.dependency_overrides.clear()

