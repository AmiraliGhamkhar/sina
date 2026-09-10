import { cn } from "@/lib/utils";

interface ToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: string;
  description?: string;
  disabled?: boolean;
  className?: string;
}

export function Toggle({
  checked,
  onChange,
  label,
  description,
  disabled,
  className,
}: ToggleProps) {
  return (
    <label
      className={cn(
        "flex items-center gap-3 cursor-pointer select-none",
        disabled && "opacity-50 cursor-not-allowed",
        className
      )}
    >
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative h-6 w-11 shrink-0 rounded-full border transition-colors duration-200 outline-none",
          checked
            ? "bg-accent-secondary/90 border-accent-secondary"
            : "bg-bg-tertiary border-border"
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 left-0.5 h-4.5 w-4.5 rounded-full bg-white shadow transition-transform duration-200",
            checked ? "translate-x-5" : "translate-x-0"
          )}
          style={{ height: "1.125rem", width: "1.125rem" }}
        />
      </button>
      {(label || description) && (
        <span className="flex flex-col">
          {label && <span className="text-sm font-medium text-text-primary">{label}</span>}
          {description && (
            <span className="text-[0.8125rem] text-text-muted">{description}</span>
          )}
        </span>
      )}
    </label>
  );
}
