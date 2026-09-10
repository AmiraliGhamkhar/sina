import { getProviderInfo } from "@/lib/utils";
import { Badge } from "./ui/Badge";

interface ModelBadgeProps {
  modelId: string | null | undefined;
  /** When true, also show the model id next to the provider name. */
  showModel?: boolean;
  className?: string;
}

export function ModelBadge({ modelId, showModel = false, className }: ModelBadgeProps) {
  const { provider, color } = getProviderInfo(modelId);
  return (
    <Badge dotColor={color} className={className}>
      {provider}
      {showModel && modelId ? (
        <span className="font-mono text-text-muted">· {modelId}</span>
      ) : null}
    </Badge>
  );
}
