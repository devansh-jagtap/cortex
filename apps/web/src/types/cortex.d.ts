export interface CortexBridge {
  /** Where the engine Electron started is listening, when Electron started one. */
  backendUrl?: string;
  /** Opens a native folder picker. Resolves to the chosen path, or null if cancelled. */
  selectFolder: () => Promise<string | null>;
  /** Opens the original photo in the OS default app. Takes an index id, never a path. */
  openImage: (id: number) => Promise<void>;
  /** Reveals the original photo in File Explorer. */
  showImageInFolder: (id: number) => Promise<void>;
}

declare global {
  interface Window {
    cortex?: CortexBridge;
  }
}

export {};
