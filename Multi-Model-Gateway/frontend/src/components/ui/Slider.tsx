import { useEffect, useId, useRef, useState } from "react";
import { cn } from "@/lib/utils";

interface SliderProps {
  label: string;
  value: number;
  min: number;
  max: number; // upper bound of the draggable track
  step?: number;
  onChange: (value: number) => void;
  /** Optional captions shown under the track, e.g. ["Precise", "Creative"]. */
  endLabels?: [string, string];
  hint?: string;
  className?: string;
  disabled?: boolean;
  /** Max value enterable via the number box (defaults to `max`). Lets you type
   *  values beyond the slider's visual cap. */
  inputMax?: number;
}

export function Slider({
  label,
  value,
  min,
  max,
  step = 1,
  onChange,
  endLabels,
  hint,
  className,
  disabled,
  inputMax,
}: SliderProps) {
  const id = useId();
  const typeMax = inputMax ?? max;
  const pct = max > min ? Math.min(Math.max(((value - min) / (max - min)) * 100, 0), 100) : 0;

  // Local text state so decimals type freely; commit (clamp) on blur / Enter.
  const [text, setText] = useState(String(value));
  const focused = useRef(false);
  useEffect(() => {
    if (!focused.current) setText(String(value));
  }, [value]);

  const commit = () => {
    const n = Number(text);
    const next = Number.isNaN(n) ? value : Math.min(Math.max(n, min), typeMax);
    onChange(next);
    setText(String(next));
  };

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <div className="flex items-baseline justify-between">
        <label htmlFor={id} className="text-sm font-medium text-text-secondary">
          {label}
        </label>
        <input
          type="number"
          min={min}
          max={Number.isFinite(typeMax) ? typeMax : undefined}
          step={step}
          value={text}
          disabled={disabled}
          onChange={(e) => setText(e.target.value)}
          onFocus={() => {
            focused.current = true;
          }}
          onBlur={() => {
            focused.current = false;
            commit();
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          }}
          className="w-20 rounded-md border border-border bg-bg-tertiary px-2 py-0.5 text-right font-mono text-[0.8125rem] text-text-primary tabular-nums outline-none focus:ring-1 focus:ring-accent-primary disabled:opacity-60"
        />
      </div>
      <input
        id={id}
        type="range"
        className="gw-slider"
        min={min}
        max={max}
        step={step}
        value={Math.min(value, max)} // pin the track at max when the typed value exceeds it
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        style={{
          background: `linear-gradient(to right, var(--color-accent-primary) ${pct}%, var(--color-bg-tertiary) ${pct}%)`,
        }}
      />
      {endLabels && (
        <div className="flex justify-between text-[0.75rem] text-text-muted">
          <span>{endLabels[0]}</span>
          <span>{endLabels[1]}</span>
        </div>
      )}
      {hint && <span className="text-[0.75rem] text-text-muted">{hint}</span>}
    </div>
  );
}
