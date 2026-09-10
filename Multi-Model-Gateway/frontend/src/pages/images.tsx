import { useEffect, useState } from "react";
import {
  ChevronDown,
  ImageIcon,
  Sparkles,
  Download,
  Maximize2,
  Copy,
  Check,
  AlertTriangle,
  RotateCw,
  X,
  Trash2,
} from "lucide-react";
import { useTemplateStore } from "@/stores/template-store";
import { useImageStore } from "@/stores/image-store";
import { useWorkflowStore } from "@/stores/workflow-store";
import { toast } from "@/stores/ui-store";
import { useImageComposer } from "@/hooks/use-image-composer";
import type { ImageResult } from "@/lib/types";
import { useResolvedImageUrl } from "@/lib/authed-image";
import { cn, aspectRatioShort, formatRelativeTime, truncate } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Textarea";
import { Slider } from "@/components/ui/Slider";
import { Toggle } from "@/components/ui/Toggle";
import { Dropdown } from "@/components/ui/Dropdown";
import { Spinner } from "@/components/ui/Spinner";
import { Skeleton } from "@/components/ui/Skeleton";
import { Modal } from "@/components/ui/Modal";
import AuthedImage from "@/components/images/AuthedImage";

function ImageCard({
  image,
  onFullscreen,
}: {
  image: ImageResult;
  onFullscreen: (url: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  // Blob URL for the download link; the rendered <img> resolves independently
  // inside AuthedImage (shared cache, so no second fetch).
  const { resolved } = useResolvedImageUrl(image.url);

  return (
    <div className="group relative overflow-hidden rounded-lg border border-border bg-bg-secondary shadow-[0_1px_3px_rgba(0,0,0,0.3)]">
      <AuthedImage
        src={image.url}
        alt={image.filename}
        className="h-full w-full object-cover transition-transform duration-200 group-hover:scale-[1.02]"
      />
      <div className="absolute right-2 top-2 flex gap-1.5 opacity-0 transition-opacity group-hover:opacity-100">
        {resolved ? (
          <a
            href={resolved}
            download={image.filename}
            target="_blank"
            rel="noopener noreferrer"
            className="flex h-8 w-8 items-center justify-center rounded-md bg-black/60 text-white backdrop-blur-sm hover:bg-black/80"
            title="Download"
          >
            <Download size={15} />
          </a>
        ) : null}
        <button
          onClick={() => onFullscreen(image.url)}
          className="flex h-8 w-8 items-center justify-center rounded-md bg-black/60 text-white backdrop-blur-sm hover:bg-black/80"
          title="Fullscreen"
        >
          <Maximize2 size={15} />
        </button>
        <button
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(image.url);
              setCopied(true);
              toast.success("Image URL copied.");
              setTimeout(() => setCopied(false), 1500);
            } catch {
              toast.error("Could not copy URL.");
            }
          }}
          className="flex h-8 w-8 items-center justify-center rounded-md bg-black/60 text-white backdrop-blur-sm hover:bg-black/80"
          title="Copy URL"
        >
          {copied ? <Check size={15} className="text-success" /> : <Copy size={15} />}
        </button>
      </div>
    </div>
  );
}

export default function ImagesPage() {
  const templates = useTemplateStore((s) => s.templates);
  const fetchTemplates = useTemplateStore((s) => s.fetchTemplates);
  const hasLoadedTemplates = useTemplateStore((s) => s.hasLoaded);
  const workflows = useWorkflowStore((s) => s.workflows);

  const {
    prompt, setPrompt,
    negative, setNegative,
    aspect, setAspect,
    steps, setSteps,
    cfg, setCfg,
    templateId, setTemplateId,
    workflowId, setWorkflowId,
    batchSize, setBatchSize,
    randomSeed, setRandomSeed,
    seed, setSeed,
    rewrite, setRewrite,
    showAdvanced, setShowAdvanced,
    aspectRatios, aspectLoading, aspectError, loadAspectRatios,
    cancel, isGenerating, status, images, rewrittenPrompt, error,
    handleGenerate,
  } = useImageComposer();

  // Generation history lives in the store so the left sidebar can show it too.
  const history = useImageStore((s) => s.history);
  const clearImageHistory = useImageStore((s) => s.clearImageHistory);

  const [fullscreen, setFullscreen] = useState<string | null>(null);

  useEffect(() => {
    if (!hasLoadedTemplates) void fetchTemplates();
  }, [hasLoadedTemplates, fetchTemplates]);

  const templateOptions = [
    { value: "", label: "None (use default)" },
    ...templates.map((t) => ({ value: t.id, label: t.name })),
  ];

  const workflowOptions = [
    { value: "", label: "Default workflow" },
    ...workflows.map((w) => ({ value: w.id, label: w.name })),
  ];

  const gridCols = images.length <= 1 ? "grid-cols-1" : "grid-cols-2";

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="border-b border-border px-6 py-4 max-md:pl-14 max-md:pr-14">
        <h1 className="text-2xl text-text-primary">Image Generation</h1>
        <p className="text-sm text-text-secondary">Text-to-image via ComfyUI</p>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(360px,440px)_1fr]">
        {/* ── Left: prompt + options ── */}
        <div className="min-h-0 overflow-y-auto border-r border-border p-6">
          <div className="flex flex-col gap-4">
            {/* Rewrite — sits above the prompt so it reads as a mode for it */}
            <div className="rounded-lg border border-border bg-bg-secondary/60 px-3 py-3">
              <Toggle
                checked={rewrite}
                onChange={setRewrite}
                label="Rewrite prompt with AI"
                description="Enhances your prompt using an AI model for better results"
              />
            </div>

            <Textarea
              label="Prompt"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Describe the image you want to generate…"
              className="min-h-[120px]"
            />
            <div className="-mt-2 text-right text-[0.75rem] text-text-muted">
              {prompt.length} chars
            </div>

            <button
              onClick={() => setShowAdvanced((s) => !s)}
              className="flex items-center justify-between rounded-lg border border-border bg-bg-secondary px-3 py-2 text-sm text-text-secondary hover:bg-bg-tertiary"
            >
              Advanced Options
              <ChevronDown
                size={16}
                className={cn("transition-transform", showAdvanced && "rotate-180")}
              />
            </button>

            {showAdvanced && (
              <div className="flex flex-col gap-5 animate-fade-in">
                <Textarea
                  label="Negative Prompt"
                  value={negative}
                  onChange={(e) => setNegative(e.target.value)}
                  className="min-h-[60px]"
                />

                {/* Aspect ratio grid (sourced from the backend) */}
                <div className="flex flex-col gap-1.5">
                  <label className="text-sm font-medium text-text-secondary">Aspect Ratio</label>
                  {aspectLoading ? (
                    <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
                      {Array.from({ length: 8 }).map((_, i) => (
                        <Skeleton key={i} className="h-9 w-full" />
                      ))}
                    </div>
                  ) : aspectError ? (
                    <div className="flex items-center justify-between gap-2 rounded-lg border border-border bg-bg-tertiary/50 px-3 py-2">
                      <span className="text-[0.8125rem] text-text-muted">
                        Couldn&apos;t load ratios — the backend default will be used.
                      </span>
                      <button
                        onClick={loadAspectRatios}
                        className="flex items-center gap-1 text-[0.8125rem] text-accent-primary hover:underline"
                      >
                        <RotateCw size={13} /> Retry
                      </button>
                    </div>
                  ) : (
                    <>
                      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
                        {aspectRatios.map((r) => (
                          <button
                            key={r}
                            onClick={() => setAspect(r)}
                            title={r}
                            className={cn(
                              "rounded-lg border px-2 py-2 text-[0.8125rem] transition-colors",
                              aspect === r
                                ? "border-accent-primary bg-accent-primary text-white"
                                : "border-border bg-bg-tertiary text-text-secondary hover:text-text-primary"
                            )}
                          >
                            {aspectRatioShort(r)}
                          </button>
                        ))}
                      </div>
                      {aspect && (
                        <span className="text-[0.75rem] text-text-muted">{aspect}</span>
                      )}
                    </>
                  )}
                </div>

                <Slider label="Steps" value={steps} min={1} max={50} step={1} onChange={setSteps} />
                <Slider label="CFG Scale" value={cfg} min={1} max={20} step={0.1} onChange={setCfg} />

                <div className="flex flex-col gap-1">
                  <label className="text-sm font-medium text-text-secondary">Prompt Template</label>
                  <Dropdown
                    value={templateId}
                    options={templateOptions}
                    onChange={setTemplateId}
                    className="w-full"
                  />
                </div>

                <div className="flex flex-col gap-1">
                  <label className="text-sm font-medium text-text-secondary">Workflow</label>
                  <Dropdown
                    value={workflowId}
                    options={workflowOptions}
                    onChange={setWorkflowId}
                    className="w-full"
                  />
                </div>

                {/* Batch size */}
                <div className="flex flex-col gap-1.5">
                  <label className="text-sm font-medium text-text-secondary">Number of images</label>
                  <div className="flex gap-1.5">
                    {[1, 2, 3, 4].map((n) => (
                      <button
                        key={n}
                        onClick={() => setBatchSize(n)}
                        className={cn(
                          "h-9 flex-1 rounded-lg border text-sm transition-colors",
                          batchSize === n
                            ? "border-accent-primary bg-accent-primary text-white"
                            : "border-border bg-bg-tertiary text-text-secondary hover:text-text-primary"
                        )}
                      >
                        {n}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Seed */}
                <div className="flex flex-col gap-2">
                  <Toggle
                    checked={randomSeed}
                    onChange={setRandomSeed}
                    label="Random seed"
                  />
                  {!randomSeed && (
                    <input
                      type="number"
                      value={seed}
                      onChange={(e) => setSeed(e.target.value)}
                      placeholder="Seed value"
                      className="h-10 w-full rounded-lg border border-border bg-bg-secondary px-3 text-sm text-text-primary placeholder:text-text-muted outline-none focus:ring-2 focus:ring-accent-primary"
                    />
                  )}
                </div>

              </div>
            )}

            <Button
              variant="primary"
              fullWidth
              onClick={handleGenerate}
              isLoading={isGenerating}
              leftIcon={!isGenerating ? <Sparkles size={16} /> : undefined}
              disabled={isGenerating}
            >
              {isGenerating ? "Generating…" : "Generate"}
            </Button>
          </div>
        </div>

        {/* ── Right: results + history ── */}
        <div className="min-h-0 overflow-y-auto p-6">
          {rewrittenPrompt && (
            <div className="mb-4 rounded-lg border border-border bg-bg-secondary/60 px-3 py-2">
              <span className="text-[0.75rem] uppercase tracking-wide text-text-muted">Prompt used</span>
              <p className="mt-0.5 font-mono text-[0.8125rem] italic leading-relaxed text-text-secondary">
                {rewrittenPrompt}
              </p>
            </div>
          )}

          {status === "idle" && (
            <div className="flex h-full min-h-[320px] flex-col items-center justify-center gap-3 text-center">
              <ImageIcon size={42} strokeWidth={1.5} className="text-text-muted" />
              <p className="text-sm text-text-secondary">Your generated images will appear here</p>
            </div>
          )}

          {status === "generating" && (
            <div className="flex h-full min-h-[320px] flex-col items-center justify-center gap-4 text-center">
              <Spinner size={32} />
              <p className="text-sm text-text-muted">Generating… ~10–15 seconds</p>
              <Button variant="secondary" onClick={cancel}>
                Cancel
              </Button>
            </div>
          )}

          {status === "error" && (
            <div className="flex h-full min-h-[320px] flex-col items-center justify-center gap-3 text-center">
              <AlertTriangle size={36} className="text-danger" />
              <p className="max-w-sm text-sm text-danger">{error ?? "Generation failed."}</p>
              <Button variant="secondary" onClick={handleGenerate}>
                Retry
              </Button>
            </div>
          )}

          {status === "complete" && images.length > 0 && (
            <div className={cn("grid gap-3 animate-fade-in", gridCols)}>
              {images.map((img) => (
                <ImageCard key={img.filename} image={img} onFullscreen={setFullscreen} />
              ))}
            </div>
          )}

          {/* History */}
          {history.length > 0 && (
            <div className="mt-8 border-t border-border pt-5">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-lg text-text-primary">Recent generations</h2>
                <Button
                  variant="ghost"
                  size="sm"
                  leftIcon={<Trash2 size={14} />}
                  onClick={clearImageHistory}
                >
                  Clear
                </Button>
              </div>
              <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-6">
                {history.map((h) => {
                  const thumb = h.images[0];
                  return (
                    <button
                      key={h.promptId + h.createdAt}
                      onClick={() => thumb && setFullscreen(thumb.url)}
                      className="group flex flex-col gap-1 text-left"
                      title={h.prompt}
                    >
                      {thumb ? (
                        <AuthedImage
                          src={thumb.url}
                          alt=""
                          className="aspect-square w-full rounded-md border border-border object-cover transition-transform group-hover:scale-[1.03]"
                        />
                      ) : (
                        <div className="aspect-square w-full rounded-md border border-border bg-bg-tertiary" />
                      )}
                      <span className="truncate text-[0.7rem] text-text-muted">
                        {formatRelativeTime(new Date(h.createdAt).toISOString())}
                      </span>
                      <span className="truncate text-[0.7rem] text-text-secondary">
                        {truncate(h.prompt, 28)}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Fullscreen modal */}
      <Modal open={fullscreen !== null} onClose={() => setFullscreen(null)} className="max-w-[90vw]">
        {fullscreen && (
          <div className="relative">
            <button
              onClick={() => setFullscreen(null)}
              className="absolute right-2 top-2 z-10 flex h-9 w-9 items-center justify-center rounded-md bg-black/60 text-white hover:bg-black/80"
              aria-label="Close"
            >
              <X size={18} />
            </button>
            <AuthedImage
              src={fullscreen}
              alt="Generated"
              className="max-h-[80vh] w-auto rounded-lg"
            />
          </div>
        )}
      </Modal>
    </div>
  );
}
