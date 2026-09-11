import { app, BrowserWindow, dialog, ipcMain } from "electron";
import path from "node:path";

// In dev, the renderer is the Next.js dev server. In a packaged build this
// will instead point at the static export bundled with the app.
const DEV_WEB_URL = process.env.CORTEX_WEB_URL ?? "http://localhost:3000";
const isDev = !app.isPackaged;

function createMainWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    backgroundColor: "#0a0a0a",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  if (isDev) {
    win.loadURL(DEV_WEB_URL);
  } else {
    win.loadFile(path.join(__dirname, "../../web/out/index.html"));
  }

  return win;
}

function registerIpcHandlers(): void {
  // Only exposes a native directory picker — never raw filesystem access.
  // The renderer receives back exactly the path the OS dialog returned.
  ipcMain.handle("select-folder", async () => {
    const focusedWindow = BrowserWindow.getFocusedWindow() ?? undefined;
    const result = await dialog.showOpenDialog(focusedWindow!, {
      properties: ["openDirectory"],
    });

    if (result.canceled || result.filePaths.length === 0) {
      return null;
    }

    return result.filePaths[0];
  });
}

app.whenReady().then(() => {
  registerIpcHandlers();
  createMainWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
