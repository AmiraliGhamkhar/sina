import { useImageStore } from "@/stores/image-store";
import { formatRelativeTime, truncate } from "@/lib/utils";
import { useResolvedImageUrl } from "@/lib/authed-image";
import AuthedImage from "@/components/images/AuthedImage";
import type { HistoryEntry } from "@/lib/image-history";

/** One history row: authed thumbnail + link to the blob URL. */
function HistoryLink({ entry, index }: { entry: HistoryEntry; index: number }) {
  const thumb = entry.images[0];
  const { resolved } = useResolvedImageUrl(thumb ? thumb.url : "");

  return (
    <li
      key={entry.promptId + entry.createdAt}
      className="animate-slide-up"
      style={{ animationDelay: `${Math.min(index, 12) * 30}ms` }}
    >
      <a
        href={resolved ?? undefined}
        target="_blank"
        rel="noopener noreferrer"
        title={entry.prompt}
        className="group flex items-center gap-2.5 rounded-lg border-l-2 border-transparent px-2 py-1.5 transition-colors hover:bg-bg-tertiary/60"
      >
        {thumb ? (
          <AuthedImage
            src={thumb.url}
            alt=""
            className="h-10 w-10 shrink-0 rounded-md border border-border object-cover"
          />
        ) : (
          <span className="h-10 w-10 shrink-0 rounded-md border border-border bg-bg-tertiary" />
        )}
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="truncate text-sm text-text-primary">{truncate(entry.prompt, 30)}</span>
          <span className="text-[0.7rem] text-text-muted">
            {formatRelativeTime(new Date(entry.createdAt).toISOString())}
          </span>
        </span>
      </a>
    </li>
  );
}

/** Recent image generations for the left sidebar (Image mode). */
export function ImageHistoryList() {
  const history = useImageStore((s) => s.history);

  if (history.length === 0) {
    return <p className="px-3 py-8 text-center text-sm text-text-muted">No images yet</p>;
  }

  return (
    <ul className="flex flex-col gap-0.5 py-1">
      {history.map((h, i) => (
        <HistoryLink key={h.promptId + h.createdAt} entry={h} index={i} />
      ))}
    </ul>
  );
}
