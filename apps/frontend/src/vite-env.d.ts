/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Chrome extension ID; enables one-click pairing via externally_connectable. */
  readonly VITE_EXTENSION_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
