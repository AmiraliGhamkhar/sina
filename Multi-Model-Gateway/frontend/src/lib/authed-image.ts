import { useEffect, useState } from "react";
import { imageApi } from "./api-endpoints";

// backend path -> object URL. Shared across the whole SPA so a generated image
// (grid card, history thumb, fullscreen modal) is fetched from the network
// once and then re-rendered from cache. Object URLs are deliberately never
// revoked — a page reload clears them.
const blobCache = new Map<string, string>();

/** Resolve a backend image path to a blob object URL (fetched with auth). */
export async function resolveImageUrl(path: string): Promise<string> {
  const cached = blobCache.get(path);
  if (cached) return cached;
  if (!path.startsWith("/v1/images/")) {
    throw new Error("unresolvable image URL");
  }
  const { blob } = await imageApi.fetchBlob(path);
  const url = URL.createObjectURL(blob);
  blobCache.set(path, url);
  return url;
}

/**
 * Download a backend file (e.g. a file from the backend) through the
 * authed API: fetch as a blob, then trigger a browser download under the
 * given filename via a temporary anchor.
 */
export async function downloadAuthedFile(path: string, filename: string): Promise<void> {
  const { blob } = await imageApi.fetchBlob(path);
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}

interface ResolvedImageState {
  path: string;
  resolved: string | null;
  failed: boolean;
}

/**
 * React binding for resolveImageUrl: re-resolves when the path changes.
 * `failed` is set when the path can't be resolved (unrecognized shape,
 * network error, or a 401 the refresh flow couldn't recover from).
 *
 * State only changes in async callbacks (never synchronously in the effect),
 * so a path swap immediately renders the loading state and the stale path's
 * result can never overwrite the new path.
 */
export function useResolvedImageUrl(path: string): {
  resolved: string | null;
  failed: boolean;
} {
  const [state, setState] = useState<ResolvedImageState>({
    path,
    resolved: null,
    failed: false,
  });

  useEffect(() => {
    let cancelled = false;
    resolveImageUrl(path)
      .then((url) => {
        if (!cancelled) {
          setState((prev) =>
            prev.path === path ? { path, resolved: url, failed: false } : prev
          );
        }
      })
      .catch(() => {
        if (!cancelled) {
          setState((prev) =>
            prev.path === path ? { path, resolved: null, failed: true } : prev
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [path]);

  if (state.path !== path) {
    return { resolved: null, failed: false };
  }
  return { resolved: state.resolved, failed: state.failed };
}
