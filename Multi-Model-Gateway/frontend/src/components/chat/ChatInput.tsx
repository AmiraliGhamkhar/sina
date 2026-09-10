import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { ArrowUp, Square, Lock } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { usePresetStore } from "@/stores/preset-store";
import { cn } from "@/lib/utils";
import { Dropdown } from "@/components/ui/Dropdown";
import type { DropdownOption } from "@/components/ui/Dropdown";
import { ModelSelector } from "@/components/chat/ModelSelector";

const MAX_TEXTAREA_HEIGHT = 168; // ~6 lines

interface ChatInputProps {
  onSend: (content: string) => void;
  onCancel: () => void;
  isStreaming: boolean;
}

export function ChatInput({ onSend, onCancel, isStreaming }: ChatInputProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const presetId = useChatStore((s) => s.presetId);
  const setPresetId = useChatStore((s) => s.setPresetId);
  const isPrivate = useChatStore((s) => s.isPrivate);
  const setPrivate = useChatStore((s) => s.setPrivate);
  const presets = usePresetStore((s) => s.presets);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_TEXTAREA_HEIGHT)}px`;
  }, [value]);

  const presetOptions: DropdownOption[] = [
    { value: "", label: "Default preset" },
    ...presets
      .filter((p) => p.name !== "Default")
      .map((p) => ({
        value: p.id,
        label: p.name,
        sublabel: `temp ${p.temperature}`,
      })),
  ];

  const submit = () => {
    const content = value.trim();
    if (!content || isStreaming) return;
    onSend(content);
    setValue("");
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const canSend = value.trim().length > 0 && !isStreaming;

  return (
    <div className="border-t border-border bg-bg-secondary/70 px-4 py-3">
      <div className="mx-auto w-full max-w-4xl">
        {/* Composer — one box, one focus highlight; controls live inside it */}
        <div className="gw-composer flex flex-col gap-2 rounded-xl border border-transparent bg-bg-secondary px-3 py-2.5 transition-shadow hover:ring-2 hover:ring-accent-primary/70 focus-within:ring-2 focus-within:ring-accent-primary/70">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={isStreaming}
            rows={1}
            placeholder={isStreaming ? "Generating…" : "Message…"}
            className="w-full resize-none bg-transparent py-1.5 text-[0.9375rem] text-text-primary placeholder:text-text-muted outline-none focus-visible:outline-none disabled:opacity-60"
          />

          <div className="flex items-center gap-2">
            {/* left: privacy + model */}
            <button
              type="button"
              onClick={() => setPrivate(!isPrivate)}
              title="Private mode — forces the request to the local provider only"
              className={cn(
                "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-transparent transition-colors",
                isPrivate
                  ? "bg-accent-secondary/15 text-accent-secondary"
                  : "bg-transparent text-text-muted hover:bg-accent-primary hover:text-white"
              )}
              aria-pressed={isPrivate}
            >
              <Lock size={15} />
            </button>
            <ModelSelector />

            {/* right: preset · send */}
            <div className="ml-auto flex items-center gap-2">
              <Dropdown
                value={presetId ?? ""}
                options={presetOptions}
                onChange={(v) => setPresetId(v || null)}
                size="sm"
                up
                align="right"
                transparent
                className="w-[160px]"
              />
              {isStreaming ? (
                <button
                  type="button"
                  onClick={onCancel}
                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent-primary text-white transition-[filter] hover:brightness-110"
                  aria-label="Stop generating"
                >
                  <Square size={14} className="fill-current" />
                </button>
              ) : (
                <button
                  type="button"
                  onClick={submit}
                  disabled={!canSend}
                  className={cn(
                    "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-colors",
                    canSend
                      ? "bg-accent-primary text-white hover:brightness-110"
                      : "bg-transparent text-text-muted"
                  )}
                  aria-label="Send message"
                >
                  <ArrowUp size={16} />
                </button>
              )}
            </div>
          </div>
        </div>

        {isStreaming && (
          <div className="mt-2 flex items-center gap-2 px-1 text-[0.75rem] text-text-muted">
            <span className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-accent-primary" />
            Generating…
          </div>
        )}
      </div>
    </div>
  );
}
