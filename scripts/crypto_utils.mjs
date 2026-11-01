import crypto from 'node:crypto';

export const MAGIC = Buffer.from('AGENC1');
export const SALT_LEN = 16;
export const NONCE_LEN = 12;
export const TAG_LEN = 16;
export const ITERATIONS = 100000;

// Security constants for global mandatory AES-256-GCM encryption
export const NTTM_SECURE_ENCRYPT_V1_SALT = 'NTTM_SECURE_ENCRYPT_V1_SALT';
export const DEFAULT_SECURITY_SALT = NTTM_SECURE_ENCRYPT_V1_SALT;

export function deriveKey(passphrase, salt) {
  return crypto.pbkdf2Sync(passphrase, salt, ITERATIONS, 32, 'sha256');
}

/**
 * Returns user key if provided and non-empty.
 * Otherwise returns a deterministic standard fallback key derived from NTTM_SECURE_ENCRYPT_V1_SALT
 * so all payloads and envelopes are strictly and automatically encrypted.
 */
export function getEffectiveKey(userKey) {
  if (userKey && typeof userKey === 'string' && userKey.trim().length > 0) {
    return userKey.trim();
  }
  return crypto.createHash('sha256').update(NTTM_SECURE_ENCRYPT_V1_SALT).digest('hex');
}

export const get_effective_key = getEffectiveKey;

/**
 * Encrypts arbitrary buffer with AES-256-GCM using effective key.
 * Always guarantees encryption (never returns unencrypted buffer).
 */
export function encryptBytes(buffer, passphrase) {
  if (!Buffer.isBuffer(buffer)) {
    buffer = Buffer.from(buffer);
  }

  const effectivePassphrase = getEffectiveKey(passphrase);
  const salt = crypto.randomBytes(SALT_LEN);
  const iv = crypto.randomBytes(NONCE_LEN);
  const key = deriveKey(effectivePassphrase, salt);

  const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
  const ciphertext = Buffer.concat([cipher.update(buffer), cipher.final()]);
  const tag = cipher.getAuthTag();

  return Buffer.concat([MAGIC, salt, iv, ciphertext, tag]);
}

/**
 * Decrypts buffer encrypted with AES-256-GCM if it starts with AGENC1 magic header.
 * Uses effective key (user key or standard fallback).
 */
export function decryptBytes(buffer, passphrase) {
  if (!buffer || buffer.length < MAGIC.length + SALT_LEN + NONCE_LEN + TAG_LEN) {
    return buffer;
  }

  const header = buffer.subarray(0, MAGIC.length);
  if (!header.equals(MAGIC)) {
    return buffer;
  }

  const effectivePassphrase = getEffectiveKey(passphrase);

  let offset = MAGIC.length;
  const salt = buffer.subarray(offset, offset + SALT_LEN);
  offset += SALT_LEN;
  const iv = buffer.subarray(offset, offset + NONCE_LEN);
  offset += NONCE_LEN;
  const cipherAndTag = buffer.subarray(offset);

  const tag = cipherAndTag.subarray(cipherAndTag.length - TAG_LEN);
  const ciphertext = cipherAndTag.subarray(0, cipherAndTag.length - TAG_LEN);

  const key = deriveKey(effectivePassphrase, salt);
  const decipher = crypto.createDecipheriv('aes-256-gcm', key, iv);
  decipher.setAuthTag(tag);

  try {
    return Buffer.concat([decipher.update(ciphertext), decipher.final()]);
  } catch (err) {
    // If decryption with effective passphrase failed and user had custom key, try fallback default key
    const defaultKey = getEffectiveKey(null);
    if (effectivePassphrase !== defaultKey) {
      try {
        const fallbackKey = deriveKey(defaultKey, salt);
        const fallbackDecipher = crypto.createDecipheriv('aes-256-gcm', fallbackKey, iv);
        fallbackDecipher.setAuthTag(tag);
        return Buffer.concat([fallbackDecipher.update(ciphertext), fallbackDecipher.final()]);
      } catch (_) {}
    }
    throw new Error(`Failed to decrypt envelope with AES-256-GCM: ${err.message}`);
  }
}

/**
 * Enforces AES-256-GCM encryption on any envelope payload (buffer, string, or object).
 */
export function encryptEnvelope(data, passphrase) {
  let buf;
  if (Buffer.isBuffer(data)) {
    buf = data;
  } else if (typeof data === 'string') {
    buf = Buffer.from(data, 'utf8');
  } else if (typeof data === 'object' && data !== null) {
    buf = Buffer.from(JSON.stringify(data, null, 2), 'utf8');
  } else {
    throw new TypeError(`Unsupported envelope type: ${typeof data}`);
  }
  return encryptBytes(buf, getEffectiveKey(passphrase));
}

export const encrypt_envelope = encryptEnvelope;

/**
 * Decrypts an AES-256-GCM encrypted envelope buffer.
 */
export function decryptEnvelope(buffer, passphrase) {
  return decryptBytes(buffer, getEffectiveKey(passphrase));
}

export const decrypt_envelope = decryptEnvelope;

export function isEncrypted(buffer) {
  if (!buffer || buffer.length < MAGIC.length) return false;
  return buffer.subarray(0, MAGIC.length).equals(MAGIC);
}

export const is_encrypted = isEncrypted;
