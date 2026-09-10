import type { ImageResult } from "./types";

export interface HistoryEntry {
  promptId: string;
  prompt: string;
  images: ImageResult[];
  createdAt: number;
}

const KEY = "llm-gateway-image-history";
const MAX = 20;

export function loadHistory(): HistoryEntry[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function saveHistory(entries: HistoryEntry[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(entries.slice(0, MAX)));
  } catch {
    /* storage may be full or unavailable */
  }
}

export function addHistory(entry: HistoryEntry): HistoryEntry[] {
  const next = [entry, ...loadHistory().filter((e) => e.promptId !== entry.promptId)].slice(0, MAX);
  saveHistory(next);
  return next;
}

export function clearHistory(): HistoryEntry[] {
  saveHistory([]);
  return [];
}
