// Browser-only TOTP, adapted from the project's existing 2FA_landing generator.
// No secret is copied into a URL, storage, analytics or an HTTP request.

function decodeBase32(input) {
  const encoded = input.toUpperCase().replace(/\s+/g, "").replace(/=+$/, "");
  if (!/^[A-Z2-7]+$/.test(encoded) || encoded.length < 8) {
    throw new Error("Введи Base32-секрет длиной от 8 символов.");
  }

  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = 0;
  let buffer = 0;
  const bytes = [];

  for (const character of encoded) {
    buffer = (buffer << 5) | alphabet.indexOf(character);
    bits += 5;
    if (bits >= 8) {
      bits -= 8;
      bytes.push((buffer >>> bits) & 255);
      buffer &= (1 << bits) - 1;
    }
  }

  if (!bytes.length || buffer !== 0 || [1, 3, 6].includes(encoded.length % 8)) {
    throw new Error("В секрете некорректная Base32-кодировка.");
  }
  return new Uint8Array(bytes);
}

export function parseTotpSecret(raw) {
  const value = raw.trim();
  if (!value) return null;

  let encoded = value;
  let period = 30;
  let digits = 6;
  let algorithm = "SHA-1";

  if (value.toLowerCase().startsWith("otpauth://")) {
    let uri;
    try {
      uri = new URL(value);
    } catch {
      throw new Error("Не удалось прочитать otpauth-ссылку.");
    }
    if (uri.protocol !== "otpauth:" || uri.hostname.toLowerCase() !== "totp") {
      throw new Error("Поддерживаются только otpauth://totp ссылки.");
    }
    encoded = uri.searchParams.get("secret") || "";
    const requestedPeriod = Number(uri.searchParams.get("period") || "30");
    const requestedDigits = Number(uri.searchParams.get("digits") || "6");
    const requestedAlgorithm = (uri.searchParams.get("algorithm") || "SHA1").toUpperCase().replace(/-/g, "");
    if (!Number.isInteger(requestedPeriod) || requestedPeriod < 10 || requestedPeriod > 120) {
      throw new Error("Период TOTP должен быть от 10 до 120 секунд.");
    }
    if (![6, 8].includes(requestedDigits)) {
      throw new Error("Поддерживаются 6- и 8-значные TOTP-коды.");
    }
    if (!["SHA1", "SHA256", "SHA512"].includes(requestedAlgorithm)) {
      throw new Error("Поддерживаются алгоритмы SHA1, SHA256 и SHA512.");
    }
    period = requestedPeriod;
    digits = requestedDigits;
    algorithm = requestedAlgorithm.replace("SHA", "SHA-");
  }

  return { bytes: decodeBase32(encoded), period, digits, algorithm };
}

export async function importTotpKey(config) {
  if (!globalThis.crypto?.subtle) {
    throw new Error("TOTP работает только в защищённом HTTPS-контексте.");
  }
  return crypto.subtle.importKey("raw", config.bytes, { name: "HMAC", hash: config.algorithm }, false, ["sign"]);
}

export async function generateTotp(config, key, unixSeconds = Math.floor(Date.now() / 1000)) {
  const counter = BigInt(Math.floor(unixSeconds / config.period));
  const buffer = new ArrayBuffer(8);
  new DataView(buffer).setBigUint64(0, counter, false);
  const signature = new Uint8Array(await crypto.subtle.sign("HMAC", key, buffer));
  const offset = signature[signature.length - 1] & 0x0f;
  const binary = ((signature[offset] & 0x7f) << 24) |
    (signature[offset + 1] << 16) |
    (signature[offset + 2] << 8) |
    signature[offset + 3];
  return String(binary % (10 ** config.digits)).padStart(config.digits, "0");
}
