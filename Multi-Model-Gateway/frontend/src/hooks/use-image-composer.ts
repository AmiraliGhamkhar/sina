import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { imageApi } from "@/lib/api-endpoints";
import { toast } from "@/stores/ui-store";
import { useImageStore } from "@/stores/image-store";
import type { ImageGenerateRequest } from "@/lib/types";
import {
  useImageGeneration,
  type CompletedGeneration,
} from "@/hooks/use-image";

const NEG_DEFAULT = "text, watermark, blurry, low quality";

export interface ImageComposer {
  // prompt + options state
  prompt: string;
  setPrompt: Dispatch<SetStateAction<string>>;
  negative: string;
  setNegative: Dispatch<SetStateAction<string>>;
  aspect: string | null;
  setAspect: Dispatch<SetStateAction<string | null>>;
  steps: number;
  setSteps: Dispatch<SetStateAction<number>>;
  cfg: number;
  setCfg: Dispatch<SetStateAction<number>>;
  templateId: string;
  setTemplateId: Dispatch<SetStateAction<string>>;
  workflowId: string;
  setWorkflowId: Dispatch<SetStateAction<string>>;
  batchSize: number;
  setBatchSize: Dispatch<SetStateAction<number>>;
  randomSeed: boolean;
  setRandomSeed: Dispatch<SetStateAction<boolean>>;
  seed: string;
  setSeed: Dispatch<SetStateAction<string>>;
  rewrite: boolean;
  setRewrite: Dispatch<SetStateAction<boolean>>;
  showAdvanced: boolean;
  setShowAdvanced: Dispatch<SetStateAction<boolean>>;

  // aspect ratios (backend is the source of truth)
  aspectRatios: string[];
  aspectLoading: boolean;
  aspectError: boolean;
  loadAspectRatios: () => Promise<void>;

  // generation lifecycle (from useImageGeneration)
  generate: (req: ImageGenerateRequest, onComplete?: (r: CompletedGeneration) => void) => Promise<void>;
  cancel: () => void;
  isGenerating: boolean;
  status: ReturnType<typeof useImageGeneration>["status"];
  images: ReturnType<typeof useImageGeneration>["images"];
  rewrittenPrompt: ReturnType<typeof useImageGeneration>["rewrittenPrompt"];
  error: ReturnType<typeof useImageGeneration>["error"];

  // actions
  handleGenerate: () => void;
  onComplete: (result: CompletedGeneration) => void;
}

/**
 * Owns the image-composer state (form fields, aspect ratios, generation wiring)
 * so `pages/images.tsx` stays a pure render of the composer + results. The
 * mutation side of the "New Image" nonce reset and the generation
 * `handleGenerate`/`onComplete` live here; the page only binds them to UI.
 */
export function useImageComposer(): ImageComposer {
  const [prompt, setPrompt] = useState("");
  const [negative, setNegative] = useState(NEG_DEFAULT);
  const [aspect, setAspect] = useState<string | null>(null);
  const [steps, setSteps] = useState(10);
  const [cfg, setCfg] = useState(1.2);
  const [templateId, setTemplateId] = useState("");
  const [workflowId, setWorkflowId] = useState("");
  const [batchSize, setBatchSize] = useState(1);
  const [randomSeed, setRandomSeed] = useState(true);
  const [seed, setSeed] = useState("");
  const [rewrite, setRewrite] = useState(true);
  const [showAdvanced, setShowAdvanced] = useState(true);

  const [aspectRatios, setAspectRatios] = useState<string[]>([]);
  const [aspectLoading, setAspectLoading] = useState(true);
  const [aspectError, setAspectError] = useState(false);

  const { generate, cancel, reset, isGenerating, status, images, rewrittenPrompt, error } =
    useImageGeneration();

  const newImageNonce = useImageStore((s) => s.newImageNonce);
  const initialNonce = useRef(newImageNonce);
  const addImageHistory = useImageStore((s) => s.addImageHistory);

  // Reset to a blank composer when "New Image" is clicked (skip the initial
  // mount value, where the form is already fresh).
  useEffect(() => {
    if (newImageNonce === initialNonce.current) return;
    setPrompt("");
    reset();
  }, [newImageNonce, reset]);

  const loadAspectRatios = useCallback(async () => {
    setAspectLoading(true);
    setAspectError(false);
    try {
      const { aspect_ratios, default: def } = await imageApi.aspectRatios();
      setAspectRatios(aspect_ratios);
      // Default the selection to the backend default (unless the user already picked).
      setAspect((cur) => cur ?? def ?? aspect_ratios[0] ?? null);
    } catch {
      setAspectError(true);
    } finally {
      setAspectLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAspectRatios();
  }, [loadAspectRatios]);

  const onComplete = useCallback(
    (result: CompletedGeneration) => {
      addImageHistory({
        promptId: result.promptId,
        prompt: result.rewrittenPrompt,
        images: result.images,
        createdAt: Date.now(),
      });
    },
    [addImageHistory]
  );

  const handleGenerate = useCallback(() => {
    if (!prompt.trim()) {
      toast.error("Enter a prompt first.");
      return;
    }
    void generate(
      {
        prompt: prompt.trim(),
        negative_prompt: negative.trim() || NEG_DEFAULT,
        template_id: templateId || null,
        workflow_id: workflowId || null,
        steps,
        cfg,
        // Omit when unknown so the backend applies its own default.
        ...(aspect ? { aspect_ratio: aspect } : {}),
        batch_size: batchSize,
        seed: randomSeed ? null : seed.trim() ? Number(seed) : null,
        rewrite,
      },
      onComplete
    );
  }, [prompt, negative, templateId, workflowId, steps, cfg, aspect, batchSize, randomSeed, seed, rewrite, generate, onComplete]);

  return {
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
    generate, cancel, isGenerating, status, images, rewrittenPrompt, error,
    handleGenerate, onComplete,
  };
}
