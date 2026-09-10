/**
 * Shared vitest environment for unit tests in this frontend.
 *
 * The api-client module imports the zustand auth store, whose `persist`
 * middleware reads `localStorage` at module-load (rehydration). Node's test
 * environment has no `localStorage`, so we install a minimal in-memory stub
 * here — in `setupFiles`, which runs before any test module imports it.
 *
 * A plain node environment is used (no jsdom) on purpose: the api-client
 * tests exercise HTTP/SSE primitives, not the DOM, and node's built-in
 * `fetch`, `Response`, `ReadableStream`, `Blob` and `crypto` cover them.
 */
const storage = new Map<string, string>();

const localStorageStub: Storage = {
  get length() {
    return storage.size;
  },
  clear: () => storage.clear(),
  getItem: (key: string) => storage.get(key) ?? null,
  key: (index: number) => [...storage.keys()][index] ?? null,
  removeItem: (key: string) => void storage.delete(key),
  setItem: (key: string, value: string) => void storage.set(key, String(value)),
};

// `declare global` keeps TS happy without touching the DOM lib types.
// We also define `window` so zustand's `persist` middleware treats this as a
// browser environment and uses localStorageStub instead of logging
// "storage is currently unavailable" on every write.
const g = globalThis as Record<string, unknown>;
g.localStorage = localStorageStub;
g.window = g;
