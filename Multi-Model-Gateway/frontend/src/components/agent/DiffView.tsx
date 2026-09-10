/** Minimal unified-diff renderer — green/red lines, no external dep. */
export function DiffView({ patch }: { patch: string }) {
  const lines = patch.split("\n");
  return (
    <pre className="overflow-auto rounded bg-bg-primary p-2 font-mono text-[0.7rem] leading-relaxed">
      {lines.map((line, i) => {
        const tone =
          line.startsWith("+") && !line.startsWith("+++")
            ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
            : line.startsWith("-") && !line.startsWith("---")
              ? "bg-red-500/10 text-red-600 dark:text-red-300"
              : line.startsWith("@@")
                ? "text-text-muted"
                : "text-text-secondary";
        return (
          <div key={i} className={tone}>
            {line || "\u00A0"}
          </div>
        );
      })}
    </pre>
  );
}
