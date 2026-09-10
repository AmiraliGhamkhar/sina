import { forwardRef, useId } from "react";
import type { TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  hint?: string;
  mono?: boolean;
  containerClassName?: string;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ label, hint, mono, containerClassName, className, id, ...props }, ref) => {
    const autoId = useId();
    const taId = id ?? autoId;
    return (
      <div className={cn("flex flex-col gap-1", containerClassName)}>
        {label && (
          <label htmlFor={taId} className="text-sm font-medium text-text-secondary">
            {label}
          </label>
        )}
        <textarea
          ref={ref}
          id={taId}
          className={cn(
            "w-full rounded-lg bg-bg-secondary border border-border text-text-primary placeholder:text-text-muted",
            "px-3 py-2.5 text-sm leading-relaxed outline-none transition-colors resize-y",
            "focus:ring-2 focus:ring-accent-primary focus:border-transparent",
            mono && "font-mono text-[0.85rem]",
            className
          )}
          {...props}
        />
        {hint && <span className="text-[0.8125rem] text-text-muted">{hint}</span>}
      </div>
    );
  }
);
Textarea.displayName = "Textarea";
