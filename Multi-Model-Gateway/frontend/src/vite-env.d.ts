/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL: string
  readonly VITE_API_PREFIX: string
  readonly VITE_COMFYUI_HOST: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
