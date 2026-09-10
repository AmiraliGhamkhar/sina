/** Tiny classNames joiner (truthy strings/array values only). */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

/** Relative timestamp: "just now", "5m ago", "2h ago", "3d ago", or a date. */
export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return "";
  // Backend timestamps are naive UTC (datetime.utcnow); treat as UTC.
  const normalized = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`;
  const then = new Date(normalized).getTime();
  if (Number.isNaN(then)) return "";

  const diff = Date.now() - then;
  const sec = Math.floor(diff / 1000);
  if (sec < 45) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}d ago`;
  const wk = Math.floor(day / 7);
  if (wk < 5) return `${wk}w ago`;
  return new Date(normalized).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

/** Compact integer formatting: 1234 → "1.2K". */
export function formatCompact(n: number | null | undefined): string {
  if (n == null) return "0";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n % 1000 === 0 ? 0 : 1)}K`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

/** Context-length badge: 128000 → "128K tokens", 1000000 → "1M tokens". */
export function formatContextLength(n: number | null | undefined): string {
  if (!n) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M tokens`;
  if (n >= 1000) return `${Math.round(n / 1000)}K tokens`;
  return `${n} tokens`;
}

/** Decode a JWT payload without verifying the signature. */
function decodeJwt(token: string): Record<string, unknown> | null {
  try {
    const part = token.split(".")[1];
    if (!part) return null;
    const base64 = part.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), "=");
    const json = atob(padded);
    return JSON.parse(json);
  } catch {
    return null;
  }
}

/** True if the access token is missing/expired (with a small skew buffer). */
export function isTokenExpired(token: string | null, skewSeconds = 30): boolean {
  if (!token) return true;
  const payload = decodeJwt(token);
  const exp = payload?.exp;
  if (typeof exp !== "number") return true;
  return Date.now() / 1000 >= exp - skewSeconds;
}

export interface ProviderInfo {
  provider: string;
  color: string;
}

/** Infer provider + dot colour from a model id (see doc 07). */
export function getProviderInfo(modelId: string | null | undefined): ProviderInfo {
  const id = modelId ?? "";
  if (id.includes("/")) return { provider: "OpenRouter", color: "#FF653F" };
  if (id.includes("embed")) return { provider: "Embedding", color: "#FFC85C" };
  return { provider: "Local", color: "#30A46C" };
}

export const PROVIDER_DOT: Record<string, string> = {
  auto: "#FFC85C",
  local: "#30A46C",
  openrouter: "#FF653F",
};

/**
 * Short label for a ratio button, e.g. "9:16 (Portrait Widescreen)" → "9:16".
 * The list of valid ratios is owned by the backend (GET /v1/images/aspect-ratios),
 * not hardcoded here.
 */
export function aspectRatioShort(value: string): string {
  return value.split(" ")[0];
}

/** Truncate a string to n chars with an ellipsis. */
export function truncate(text: string, n: number): string {
  if (text.length <= n) return text;
  return text.slice(0, n).trimEnd() + "…";
}
