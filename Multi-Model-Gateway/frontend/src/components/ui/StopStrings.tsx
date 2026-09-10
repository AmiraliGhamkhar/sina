import { useState } from "react";
import type { KeyboardEvent } from "react";
import { X } from "lucide-react";

interface StopStringsProps {
  value: string[];
  onChange: (value: string[]) => void;
}

export function StopStrings({ value, onChange }: StopStringsProps) {
  const [draft, setDraft] = useState("");

  const add = () => {
    const v = draft.trim();
    if (!v || value.includes(v)) {
      setDraft("");
      return;
    }
    onChange([...value, v]);
    setDraft("");
  };

  const remove = (s: string) => onChange(value.filter((x) => x !== s));

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      add();
    } else if (e.key === "Backspace" && !draft && value.length) {
      remove(value[value.length - 1]);
    }
  };

  return (
    <div className="flex flex-col gap-1">
      <label className="text-sm font-medium text-text-secondary">Stop Sequences</label>
      <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-border bg-bg-secondary px-2 py-2 focus-within:ring-2 focus-within:ring-accent-primary">
        {value.map((s) => (
          <span
            key={s}
            className="inline-flex items-center gap-1 rounded-md bg-bg-tertiary px-2 py-0.5 font-mono text-[0.8125rem] text-text-primary"
          >
            {s}
            <button
              type="button"
              onClick={() => remove(s)}
              className="text-text-muted hover:text-danger"
              aria-label={`Remove ${s}`}
            >
              <X size={12} />
            </button>
          </span>
        ))}
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          onBlur={add}
          placeholder={value.length ? "" : "Type and press Enter…"}
          className="min-w-[120px] flex-1 bg-transparent px-1 text-sm text-text-primary placeholder:text-text-muted outline-none"
        />
      </div>
    </div>
  );
}
