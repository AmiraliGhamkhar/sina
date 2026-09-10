import { useCallback, useRef, useState } from "react";
import { imageApi } from "@/lib/api-endpoints";
import { ApiError } from "@/lib/api-client";
import type { ImageGenerateRequest, ImageResult } from "@/lib/types";

type Status = "idle" | "generating" | "complete" | "error";

const POLL_INTERVAL_MS = 2000;
const MAX_POLLS = 90; // ~3 minutes

export interface CompletedGeneration {
  promptId: string;
  rewrittenPrompt: string;
  images: ImageResult[];
}

export function useImageGeneration() {
  const [status, setStatus] = useState<Status>("idle");
  const [images, setImages] = useState<ImageResult[]>([]);
  const [rewrittenPrompt, setRewrittenPrompt] = useState<string | null>(null);
  const [promptId, setPromptId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef(false);

  const stopPolling = useCallback(() => {
    abortRef.current = true;
    if (pollRef.current) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const generate = useCallback(
    async (
      request: ImageGenerateRequest,
      onComplete?: (result: CompletedGeneration) => void
    ) => {
      stopPolling();
      abortRef.current = false;
      setStatus("generating");
      setError(null);
      setImages([]);
      setRewrittenPrompt(null);
      setPromptId(null);

      let result: { prompt_id: string; rewritten_prompt: string };
      try {
        result = await imageApi.generate(request);
      } catch (err) {
        setStatus("error");
        setError(err instanceof ApiError ? err.detail : "Generation failed");
        return;
      }

      setPromptId(result.prompt_id);
      setRewrittenPrompt(result.rewritten_prompt);

      let polls = 0;
      const poll = async () => {
        if (abortRef.current) return;
        try {
          const res = await imageApi.status(result.prompt_id);
          if (abortRef.current) return;
          if (res.status === "complete") {
            const imgs = res.images ?? [];
            setImages(imgs);
            setStatus("complete");
            onComplete?.({
              promptId: result.prompt_id,
              rewrittenPrompt: result.rewritten_prompt,
              images: imgs,
            });
            return;
          }
          polls += 1;
          if (polls >= MAX_POLLS) {
            setStatus("error");
            setError("Generation timed out (3 minutes).");
            return;
          }
          pollRef.current = setTimeout(poll, POLL_INTERVAL_MS);
        } catch (err) {
          if (abortRef.current) return;
          setStatus("error");
          setError(err instanceof ApiError ? err.detail : "Status check failed");
        }
      };

      pollRef.current = setTimeout(poll, POLL_INTERVAL_MS);
    },
    [stopPolling]
  );

  const cancel = useCallback(() => {
    stopPolling();
    setStatus("idle");
  }, [stopPolling]);

  const reset = useCallback(() => {
    stopPolling();
    setStatus("idle");
    setImages([]);
    setRewrittenPrompt(null);
    setPromptId(null);
    setError(null);
  }, [stopPolling]);

  return {
    generate,
    cancel,
    reset,
    isGenerating: status === "generating",
    status,
    images,
    rewrittenPrompt,
    promptId,
    error,
  };
}
