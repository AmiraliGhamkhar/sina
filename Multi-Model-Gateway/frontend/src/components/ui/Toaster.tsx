import { createPortal } from "react-dom";
import { CheckCircle2, XCircle, Info, X } from "lucide-react";
import { useUIStore } from "@/stores/ui-store";
import type { ToastKind } from "@/stores/ui-store";
import { cn } from "@/lib/utils";

const ICONS: Record<ToastKind, typeof Info> = {
  success: CheckCircle2,
  error: XCircle,
  info: Info,
};

const ACCENT: Record<ToastKind, string> = {
  success: "text-success",
  error: "text-danger",
  info: "text-accent-secondary",
};

export function Toaster() {
  const toasts = useUIStore((s) => s.toasts);
  const dismiss = useUIStore((s) => s.dismissToast);

  return createPortal(
    <div className="pointer-events-none fixed bottom-5 right-5 z-[60] flex w-[340px] max-w-[calc(100vw-2.5rem)] flex-col gap-2">
      {toasts.map((t) => {
        const Icon = ICONS[t.kind];
        return (
          <div
            key={t.id}
            className={cn(
              "pointer-events-auto flex items-start gap-3 rounded-xl border border-border bg-bg-secondary px-4 py-3",
              "shadow-[0_12px_40px_-12px_rgba(0,0,0,0.7)] animate-slide-up"
            )}
          >
            <Icon size={18} className={cn("mt-0.5 shrink-0", ACCENT[t.kind])} />
            <p className="flex-1 text-sm text-text-primary">{t.message}</p>
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              className="shrink-0 text-text-muted hover:text-text-primary transition-colors"
              aria-label="Dismiss"
            >
              <X size={16} />
            </button>
          </div>
        );
      })}
    </div>,
    document.body
  );
}
