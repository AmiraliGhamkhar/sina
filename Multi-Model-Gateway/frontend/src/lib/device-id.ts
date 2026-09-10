/**
 * Persistent per-browser device identifier used to bind refresh tokens to a
 * single client (replay protection). The id lives in its own localStorage key
 * — deliberately NOT in the zustand auth store, so it survives logout/login
 * and is never cleared by `forceLogout()`.
 */

const STORAGE_KEY = "llm-gateway-device-id";

/**
 * Module-level in-memory cache. Guarantees a stable id within the session even
 * when localStorage is unavailable (blocked storage, private mode), so login
 * and refresh always send the same device_id.
 */
let cachedDeviceId: string | null = null;

function generateDeviceId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // Fallback for older browsers / non-secure contexts without crypto.randomUUID
  return `dev-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function getDeviceId(): string {
  if (cachedDeviceId) return cachedDeviceId;

  try {
    const existing = localStorage.getItem(STORAGE_KEY);
    if (existing) {
      cachedDeviceId = existing;
      return existing;
    }
  } catch {
    /* localStorage unavailable — fall through to an in-memory id */
  }

  cachedDeviceId = generateDeviceId();
  try {
    localStorage.setItem(STORAGE_KEY, cachedDeviceId);
  } catch {
    /* storage full/blocked — the in-memory id still works for this session */
  }
  return cachedDeviceId;
}
