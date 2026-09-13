import base64
import binascii
import hashlib
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialConfigurationError(RuntimeError):
    pass


class CredentialDecryptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class EncryptedCredential:
    ciphertext: str
    nonce: str


class CredentialCipher:
    """Encrypt repository credentials with context-bound AES-256-GCM envelopes."""

    def __init__(self, encoded_key: str) -> None:
        try:
            key = base64.b64decode(encoded_key.encode(), altchars=b"-_", validate=True)
        except (ValueError, binascii.Error) as error:
            raise CredentialConfigurationError(
                "Credential encryption key must be URL-safe base64."
            ) from error
        if len(key) != 32:
            raise CredentialConfigurationError(
                "Credential encryption key must decode to exactly 32 bytes."
            )
        self._cipher = AESGCM(key)

    @staticmethod
    def _context(workspace_id: str, repository_id: str) -> bytes:
        return f"code-genome\x1f{workspace_id}\x1f{repository_id}\x1fgithub".encode()

    def encrypt(self, token: str, workspace_id: str, repository_id: str) -> EncryptedCredential:
        if not token or "\x00" in token or "\n" in token or "\r" in token:
            raise ValueError("Credential contains invalid characters.")
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(
            nonce, token.encode(), self._context(workspace_id, repository_id)
        )
        return EncryptedCredential(
            ciphertext=base64.urlsafe_b64encode(ciphertext).decode(),
            nonce=base64.urlsafe_b64encode(nonce).decode(),
        )

    def decrypt(self, envelope: EncryptedCredential, workspace_id: str, repository_id: str) -> str:
        try:
            nonce = base64.b64decode(envelope.nonce.encode(), altchars=b"-_", validate=True)
            ciphertext = base64.b64decode(
                envelope.ciphertext.encode(), altchars=b"-_", validate=True
            )
            plaintext = self._cipher.decrypt(
                nonce, ciphertext, self._context(workspace_id, repository_id)
            )
            return plaintext.decode()
        except (ValueError, UnicodeDecodeError, binascii.Error, InvalidTag) as error:
            raise CredentialDecryptionError(
                "Repository credential could not be decrypted."
            ) from error


def public_metadata_hash(token_kind: str, scopes: list[str], connected: bool) -> str:
    material = "\x1f".join([token_kind, *sorted(scopes), str(connected)])
    return hashlib.sha256(material.encode()).hexdigest()
