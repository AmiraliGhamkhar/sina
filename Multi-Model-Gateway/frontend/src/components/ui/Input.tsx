import { forwardRef, useId, useState } from "react";
import type { InputHTMLAttributes, ReactNode } from "react";
import { Eye, EyeOff } from "lucide-react";
import { cn } from "@/lib/utils";

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  hint?: string;
  error?: string;
  leftIcon?: ReactNode;
  rightSlot?: ReactNode;
  containerClassName?: string;
}

const fieldBase =
  "w-full rounded-lg bg-bg-secondary border text-text-primary placeholder:text-text-muted " +
  "transition-colors focus:ring-2 focus:ring-accent-primary focus:border-transparent outline-none";

export const Input = forwardRef<HTMLInputElement, InputProps>(
  (
    { label, hint, error, leftIcon, rightSlot, containerClassName, className, id, ...props },
    ref
  ) => {
    const autoId = useId();
    const inputId = id ?? autoId;
    return (
      <div className={cn("flex flex-col gap-1", containerClassName)}>
        {label && (
          <label htmlFor={inputId} className="text-sm font-medium text-text-secondary">
            {label}
          </label>
        )}
        <div className="relative">
          {leftIcon && (
            <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-muted">
              {leftIcon}
            </span>
          )}
          <input
            ref={ref}
            id={inputId}
            className={cn(
              fieldBase,
              "h-10 text-sm",
              leftIcon ? "pl-9" : "pl-3",
              rightSlot ? "pr-10" : "pr-3",
              error ? "border-danger" : "border-border",
              className
            )}
            {...props}
          />
          {rightSlot && (
            <span className="absolute right-2 top-1/2 -translate-y-1/2">{rightSlot}</span>
          )}
        </div>
        {error ? (
          <span className="text-[0.8125rem] text-danger">{error}</span>
        ) : hint ? (
          <span className="text-[0.8125rem] text-text-muted">{hint}</span>
        ) : null}
      </div>
    );
  }
);
Input.displayName = "Input";

interface PasswordInputProps extends Omit<InputProps, "type" | "rightSlot"> {}

export const PasswordInput = forwardRef<HTMLInputElement, PasswordInputProps>(
  (props, ref) => {
    const [show, setShow] = useState(false);
    return (
      <Input
        ref={ref}
        type={show ? "text" : "password"}
        rightSlot={
          <button
            type="button"
            tabIndex={-1}
            onClick={() => setShow((s) => !s)}
            className="flex h-7 w-7 items-center justify-center rounded-md text-text-muted hover:text-text-primary transition-colors"
            aria-label={show ? "Hide password" : "Show password"}
          >
            {show ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        }
        {...props}
      />
    );
  }
);
PasswordInput.displayName = "PasswordInput";
