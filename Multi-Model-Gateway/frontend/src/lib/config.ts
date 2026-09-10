// Centralised runtime configuration sourced from Vite env vars.
export const API_URL = import.meta.env.VITE_API_URL || "http://localhost:2727";
export const API_PREFIX = import.meta.env.VITE_API_PREFIX || "";
export const COMFYUI_HOST =
  import.meta.env.VITE_COMFYUI_HOST || "http://localhost:8188";
