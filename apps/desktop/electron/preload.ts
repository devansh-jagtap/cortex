import { contextBridge, ipcRenderer } from "electron";

// The only surface exposed to the renderer. Anything the UI needs from
// the OS or filesystem must be added here explicitly — never widen this
// by exposing ipcRenderer or Node APIs directly.
contextBridge.exposeInMainWorld("cortex", {
  selectFolder: (): Promise<string | null> => ipcRenderer.invoke("select-folder"),
});
