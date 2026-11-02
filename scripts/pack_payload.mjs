import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { execSync } from 'node:child_process';
import { encryptBytes, getEffectiveKey } from './crypto_utils.mjs';

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

// 2. High-speed zipping of work/ directory (Optimal compression)
console.log('[Packer] Archiving work/ directory with optimal compression...');
if (process.platform === 'win32') {
  execSync(`powershell -NoProfile -Command "Add-Type -AssemblyName System.IO.Compression.FileSystem; [System.IO.Compression.ZipFile]::CreateFromDirectory('${workDir.replace(/'/g, "''")}', '${payloadZip.replace(/'/g, "''")}', [System.IO.Compression.CompressionLevel]::Optimal, $false)"`, { stdio: 'ignore' });
} else {
  execSync(`cd "${workDir}" && zip -q -6 -r "${payloadZip}" . && cd "${cwd}"`, { stdio: 'ignore' });
}

// 3. Mandatory AES-256-GCM encryption of payload.zip using effective key
let zipBuffer = fs.readFileSync(payloadZip);
const effectiveKey = getEffectiveKey(encryptionKey);
console.log('[Packer] 🔒 Encrypting payload.zip with AES-256-GCM (Protecting pages, sessions & downloaded files)...');
const encryptedZip = encryptBytes(zipBuffer, effectiveKey);
fs.writeFileSync(payloadZip, encryptedZip);
zipBuffer = encryptedZip;
console.log('[Packer] ✅ Archive encrypted successfully with AES-256-GCM! Ciphertext size:', zipBuffer.length);

// 4. Split into 50MB chunks with in-flight SHA-256 calculation (Zero-redundancy I/O)
console.log('[Packer] Splitting archive into 50MB chunks...');
let partIndex = 1;
const parts = [];
for (let offset = 0; offset < zipBuffer.length; offset += CHUNK_SIZE) {
  const chunk = zipBuffer.subarray(offset, Math.min(offset + CHUNK_SIZE, zipBuffer.length));
  const partName = `payload.zip.part${String(partIndex).padStart(3, '0')}`;
  fs.writeFileSync(path.join(cwd, partName), chunk);
  const partSha = crypto.createHash('sha256').update(chunk).digest('hex');
  parts.push({
    filename: partName,
    size: chunk.length,
    sha256: partSha
  });
  partIndex++;
}

// 5. Generate manifest.json
const fullSha256 = crypto.createHash('sha256').update(zipBuffer).digest('hex');
const manifest = {
  request_id: requestId,
  total_size: zipBuffer.length,
  full_sha256: fullSha256,
  parts: parts
};

fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2), 'utf8');
console.log(`[Packer] build: sync cache manifest - Manifest generated with ${parts.length} part(s). Complete.`);
