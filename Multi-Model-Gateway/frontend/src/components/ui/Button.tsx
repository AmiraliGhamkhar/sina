import { forwardRef } from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  isLoading?: boolean;
  leftIcon?: ReactNode;
  fullWidth?: boolean;
}

const base =
  "inline-flex items-center justify-center gap-2 rounded-lg font-medium select-none " +
  "transition-[transform,background-color,color,border-color,box-shadow] duration-150 " +
  "active:scale-[0.98] disabled:opacity-50 disabled:pointer-events-none whitespace-nowrap";

const variants: Record<Variant, string> = {
  primary:
    "bg-accent-primary text-white hover:brightness-110 shadow-[0_2px_12px_-2px_rgba(255,101,63,0.5)] hover:enabled:scale-[1.02]",
  secondary:
    "border border-border bg-transparent text-text-primary hover:bg-bg-tertiary",
  ghost: "bg-transparent text-text-secondary hover:text-text-primary hover:bg-bg-tertiary/60",
  danger:
    "border border-transparent bg-transparent text-danger hover:bg-danger/10",
};

const sizes: Record<Size, string> = {
  sm: "h-8 px-3 text-[0.8125rem]",
  md: "h-10 px-4 text-sm",
  lg: "h-11 px-6 text-sm",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      variant = "secondary",
      size = "md",
      isLoading = false,
      leftIcon,
      fullWidth,
      className,
      children,
      disabled,
      ...props
    },
    ref
  ) => {
    return (
      <button
        ref={ref}
        disabled={disabled || isLoading}
        className={cn(
          base,
          variants[variant],
          sizes[size],
          fullWidth && "w-full",
          className
        )}
        {...props}
      >
        {isLoading ? (
          <Loader2 size={16} className="animate-spin" strokeWidth={2.5} />
        ) : (
          leftIcon
        )}
        {children}
      </button>
    );
  }
);
Button.displayName = "Button";
