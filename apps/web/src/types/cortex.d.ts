export interface CortexBridge {
  /** Opens a native folder picker. Resolves to the chosen path, or null if cancelled. */
  selectFolder: () => Promise<string | null>;
}

declare global {
  interface Window {
    cortex?: CortexBridge;
  }
}

export {};
