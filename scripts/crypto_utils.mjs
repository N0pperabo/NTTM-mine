import crypto from 'node:crypto';

const MAGIC = Buffer.from('AGENC1');
const SALT_LEN = 16;
const NONCE_LEN = 12;
const TAG_LEN = 16;
const ITERATIONS = 100000;

function deriveKey(passphrase, salt) {
  return crypto.pbkdf2Sync(passphrase, salt, ITERATIONS, 32, 'sha256');
}

export function encryptBytes(buffer, passphrase) {
  if (!passphrase || !passphrase.trim()) {
    return buffer;
  }

  const salt = crypto.randomBytes(SALT_LEN);
  const iv = crypto.randomBytes(NONCE_LEN);
  const key = deriveKey(passphrase.trim(), salt);

  const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
  const ciphertext = Buffer.concat([cipher.update(buffer), cipher.final()]);
  const tag = cipher.getAuthTag();

  return Buffer.concat([MAGIC, salt, iv, ciphertext, tag]);
}

export function decryptBytes(buffer, passphrase) {
  if (!buffer || buffer.length < MAGIC.length + SALT_LEN + NONCE_LEN + TAG_LEN) {
    return buffer;
  }

  const header = buffer.subarray(0, MAGIC.length);
  if (!header.equals(MAGIC)) {
    return buffer;
  }

  if (!passphrase || !passphrase.trim()) {
    throw new Error('Encrypted payload detected, but ENCRYPTION_KEY secret is not configured in GitHub Actions!');
  }

  let offset = MAGIC.length;
  const salt = buffer.subarray(offset, offset + SALT_LEN);
  offset += SALT_LEN;
  const iv = buffer.subarray(offset, offset + NONCE_LEN);
  offset += NONCE_LEN;
  const cipherAndTag = buffer.subarray(offset);

  const tag = cipherAndTag.subarray(cipherAndTag.length - TAG_LEN);
  const ciphertext = cipherAndTag.subarray(0, cipherAndTag.length - TAG_LEN);

  const key = deriveKey(passphrase.trim(), salt);
  const decipher = crypto.createDecipheriv('aes-256-gcm', key, iv);
  decipher.setAuthTag(tag);

  return Buffer.concat([decipher.update(ciphertext), decipher.final()]);
}

export function isEncrypted(buffer) {
  if (!buffer || buffer.length < MAGIC.length) return false;
  return buffer.subarray(0, MAGIC.length).equals(MAGIC);
}
