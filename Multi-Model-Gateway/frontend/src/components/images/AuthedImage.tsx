import { ImageOff } from "lucide-react";
import { Skeleton } from "@/components/ui/Skeleton";
import { useResolvedImageUrl } from "@/lib/authed-image";
import { cn } from "@/lib/utils";

interface AuthedImageProps {
  src: string;
  alt?: string;
  className?: string;
  onClick?: () => void;
}

/**
 * Image that loads its bytes through the authed API and renders via a blob URL.
 * Shows a shimmer skeleton while fetching and a broken-image placeholder when
 * the path can't be resolved.
 */
export default function AuthedImage({
  src,
  alt,
  className,
  onClick,
}: AuthedImageProps) {
  const { resolved, failed } = useResolvedImageUrl(src);

  if (failed) {
    return (
      <div
        className={cn(
          "flex flex-col items-center justify-center gap-1 bg-bg-tertiary text-text-muted",
          className
        )}
      >
        <ImageOff size={20} />
        <span className="text-[0.7rem]">Image unavailable</span>
      </div>
    );
  }

  if (!resolved) {
    return <Skeleton className={className} />;
  }

  return (
    <img
      src={resolved}
      alt={alt}
      className={className}
      onClick={onClick}
      loading="lazy"
    />
  );
}
