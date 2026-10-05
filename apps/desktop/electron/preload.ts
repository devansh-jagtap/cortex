import { contextBridge, ipcRenderer } from "electron";

// Where the engine Electron started is listening (null in dev mode, where
// the UI uses the dev engine's default address).
const engineUrl: string | null = ipcRenderer.sendSync("cortex:engine-url");

// The only surface exposed to the renderer. Anything the UI needs from
// the OS or filesystem must be added here explicitly — never widen this
// by exposing ipcRenderer or Node APIs directly. Photos are referred to by
// their index id; the renderer never hands a file path to the OS.
contextBridge.exposeInMainWorld("cortex", {
  backendUrl: engineUrl ?? undefined,
  selectFolder: (): Promise<string | null> => ipcRenderer.invoke("select-folder"),
  openImage: (id: number): Promise<void> => ipcRenderer.invoke("open-image", id),
  showImageInFolder: (id: number): Promise<void> => ipcRenderer.invoke("show-image-in-folder", id),
});
