import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { execSync } from 'node:child_process';
import { encryptBytes } from './crypto_utils.mjs';

const requestId = process.argv[2] || process.env.REQUEST_ID || 'req';
const encryptionKey = process.env.ENCRYPTION_KEY || '';
const cwd = process.cwd();

const workDir = path.join(cwd, 'work');
const payloadZip = path.join(cwd, 'payload.zip');
const CHUNK_SIZE = 52428800; // 50 MiB

if (!fs.existsSync(workDir)) {
  fs.mkdirSync(workDir, { recursive: true });
}

// 1. Clean previous artifacts
if (fs.existsSync(payloadZip)) fs.unlinkSync(payloadZip);
fs.readdirSync(cwd).filter(f => f.startsWith('payload.zip.part')).forEach(f => fs.unlinkSync(path.join(cwd, f)));
const manifestPath = path.join(cwd, 'manifest.json');
if (fs.existsSync(manifestPath)) fs.unlinkSync(manifestPath);

// 2. Zip work/ directory (cross-platform)
console.log('[Packer] Archiving work/ directory...');
if (process.platform === 'win32') {
  execSync(`powershell -Command "Compress-Archive -Path '${workDir}/*' -DestinationPath '${payloadZip}' -Force"`, { stdio: 'ignore' });
} else {
  execSync(`cd "${workDir}" && zip -r "${payloadZip}" . && cd "${cwd}"`, { stdio: 'ignore' });
}

// 3. Encrypt payload.zip if ENCRYPTION_KEY is provided
let zipBuffer = fs.readFileSync(payloadZip);
if (encryptionKey && encryptionKey.trim()) {
  console.log('[Packer] 🔒 Encrypting payload.zip with AES-256-GCM (Protecting pages, sessions & downloaded files)...');
  const encryptedZip = encryptBytes(zipBuffer, encryptionKey.trim());
  fs.writeFileSync(payloadZip, encryptedZip);
  zipBuffer = encryptedZip;
  console.log('[Packer] ✅ Archive encrypted successfully! Ciphertext size:', zipBuffer.length);
} else {
  console.log('[Packer] ENCRYPTION_KEY not set. Archive left unencrypted.');
}

// 4. Split into 50MB chunks using pure Node.js (no external split utility needed)
console.log('[Packer] Splitting archive into 50MB chunks...');
let partIndex = 1;
const partFiles = [];
for (let offset = 0; offset < zipBuffer.length; offset += CHUNK_SIZE) {
  const chunk = zipBuffer.subarray(offset, Math.min(offset + CHUNK_SIZE, zipBuffer.length));
  const partName = `payload.zip.part${String(partIndex).padStart(3, '0')}`;
  fs.writeFileSync(path.join(cwd, partName), chunk);
  partFiles.push(partName);
  partIndex++;
}

// 5. Generate manifest.json
const fullSha256 = crypto.createHash('sha256').update(zipBuffer).digest('hex');
const parts = partFiles.map(partName => {
  const buf = fs.readFileSync(path.join(cwd, partName));
  return {
    filename: partName,
    size: buf.length,
    sha256: crypto.createHash('sha256').update(buf).digest('hex')
  };
});

const manifest = {
  request_id: requestId,
  total_size: zipBuffer.length,
  full_sha256: fullSha256,
  parts: parts
};

fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2), 'utf8');
console.log(`[Packer] Manifest generated with ${parts.length} part(s). Complete.`);
