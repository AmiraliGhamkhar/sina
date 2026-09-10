import { useEffect } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
  closeOnOverlay?: boolean;
}

export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  className,
  closeOnOverlay = true,
}: ModalProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
    >
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm animate-fade-in"
        onClick={closeOnOverlay ? onClose : undefined}
      />
      <div
        className={cn(
          "relative z-10 w-full max-w-[480px] rounded-xl border border-border bg-bg-secondary",
          "shadow-[0_20px_60px_-15px_rgba(0,0,0,0.7)] animate-scale-in",
          className
        )}
      >
        {title && (
          <div className="flex items-center justify-between gap-4 border-b border-border px-5 py-4">
            <h3 className="text-lg text-text-primary">{title}</h3>
            <button
              type="button"
              onClick={onClose}
              className="flex h-8 w-8 items-center justify-center rounded-md text-text-muted hover:bg-bg-tertiary hover:text-text-primary transition-colors"
              aria-label="Close"
            >
              <X size={18} />
            </button>
          </div>
        )}
        <div className="px-5 py-4">{children}</div>
        {footer && (
          <div className="flex items-center justify-end gap-2 border-t border-border px-5 py-4">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}
