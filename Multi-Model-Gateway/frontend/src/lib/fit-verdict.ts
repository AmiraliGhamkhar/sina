// ============================================================
// Shared fit-verdict badge styling for the Models window.
// Both the local catalog (GET /v1/cookbook) and the Hugging Face
// browser (GET /v1/hf/models/{repo_id}) score with the same semantic
// outcomes but use different verdict keys, so each map aliases one set
// of label/color styles. The HF browser additionally renders a ✕ glyph
// on "Likely too large" (added in QuantRow).
// ============================================================
import type { CookbookVerdict, HfFitVerdict } from "@/lib/types";

export interface FitVerdictStyle {
  label: string;
  cls: string;
}

const STYLES: Record<"fits" | "offload" | "tooLarge" | "cpuOnly" | "unknown", FitVerdictStyle> = {
  fits: { label: "Fits fully", cls: "bg-success/15 text-success" },
  offload: { label: "Fits with CPU offload", cls: "bg-accent-secondary/15 text-accent-secondary" },
  tooLarge: { label: "Likely too large", cls: "bg-danger/15 text-danger" },
  cpuOnly: { label: "CPU only", cls: "bg-bg-elevated/60 text-text-muted" },
  unknown: { label: "Unknown", cls: "bg-bg-elevated/60 text-text-muted" },
};

/** Local catalog verdicts (GET /v1/cookbook). */
export const LOCAL_VERDICT: Record<CookbookVerdict, FitVerdictStyle> = {
  fits_fully: STYLES.fits,
  partial_offload: STYLES.offload,
  wont_fit: STYLES.tooLarge,
  cpu_only: STYLES.cpuOnly,
  unknown: STYLES.unknown,
};

/** HF per-quant verdicts (GET /v1/hf/models/{repo_id}). */
export const HF_VERDICT: Record<HfFitVerdict, FitVerdictStyle> = {
  fits_fully: STYLES.fits,
  fits_cpu_offload: STYLES.offload,
  likely_too_large: STYLES.tooLarge,
  cpu_only: STYLES.cpuOnly,
  unknown: STYLES.unknown,
};
