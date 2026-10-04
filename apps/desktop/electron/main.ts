import { app, BrowserWindow, dialog, ipcMain, shell, type IpcMainInvokeEvent } from "electron";
import { promises as fs } from "node:fs";
import path from "node:path";

// In dev, the renderer is the Next.js dev server. In a packaged build this
// will instead point at the static export bundled with the app.
const DEV_WEB_URL = process.env.CORTEX_WEB_URL ?? "http://localhost:3000";
const BACKEND_URL = process.env.CORTEX_BACKEND_URL ?? "http://127.0.0.1:8000";
const SUPPORTED_IMAGE_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".webp"]);
const isDev = !app.isPackaged;

if (isDev) {
  app.setPath("userData", path.resolve(__dirname, "../../../storage/electron-profile"));
  app.commandLine.appendSwitch("disable-gpu");
  app.commandLine.appendSwitch("disable-gpu-compositing");
  app.commandLine.appendSwitch("disable-gpu-sandbox");
  app.commandLine.appendSwitch("in-process-gpu");
  app.commandLine.appendSwitch("disable-software-rasterizer");
  app.disableHardwareAcceleration();
}

function isAppUrl(url: string): boolean {
  try {
    const target = new URL(url);
    return isDev ? target.origin === new URL(DEV_WEB_URL).origin : target.protocol === "file:";
  } catch {
    return false;
  }
}

function openExternally(url: string): void {
  if (/^https?:\/\//i.test(url)) void shell.openExternal(url);
}

function createMainWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 720,
    minHeight: 520,
    backgroundColor: "#0e1020",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  });

  // The window only ever shows Cortex itself. Links to the outside world
  // (e.g. map attribution) open in the user's browser instead.
  win.webContents.setWindowOpenHandler(({ url }) => {
    openExternally(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    if (!isAppUrl(url)) {
      event.preventDefault();
      openExternally(url);
    }
  });

  if (isDev) {
    win.loadURL(DEV_WEB_URL);
  } else {
    win.loadFile(path.join(__dirname, "../../web/out/index.html"));
  }

  return win;
}

function assertTrustedSender(event: IpcMainInvokeEvent): void {
  const url = event.senderFrame?.url;
  if (!url || !isAppUrl(url)) {
    throw new Error("Untrusted IPC sender");
  }
}

// The renderer only ever sends an image id. The path comes from the backend's
// index and is checked here before the OS is asked to do anything with it.
async function resolveImagePath(id: unknown): Promise<string> {
  if (typeof id !== "number" || !Number.isSafeInteger(id) || id <= 0) {
    throw new Error("Invalid image id");
  }
  const res = await fetch(`${BACKEND_URL}/images/${id}/metadata`);
  if (!res.ok) throw new Error("This photo is no longer in the index");
  const { path: filePath } = (await res.json()) as { path?: unknown };
  if (typeof filePath !== "string" || !path.isAbsolute(filePath)) {
    throw new Error("The index returned an invalid path");
  }
  if (!SUPPORTED_IMAGE_EXTENSIONS.has(path.extname(filePath).toLowerCase())) {
    throw new Error("Not a supported image file");
  }
  const stat = await fs.stat(filePath).catch(() => null);
  if (!stat?.isFile()) throw new Error("The original file no longer exists");
  return filePath;
}

function registerIpcHandlers(): void {
  // Only exposes a native directory picker — never raw filesystem access.
  // The renderer receives back exactly the path the OS dialog returned.
  ipcMain.handle("select-folder", async (event) => {
    assertTrustedSender(event);
    const focusedWindow = BrowserWindow.getFocusedWindow() ?? undefined;
    const result = await dialog.showOpenDialog(focusedWindow!, {
      properties: ["openDirectory"],
    });

    if (result.canceled || result.filePaths.length === 0) {
      return null;
    }

    return result.filePaths[0];
  });

  ipcMain.handle("open-image", async (event, id: unknown) => {
    assertTrustedSender(event);
    const error = await shell.openPath(await resolveImagePath(id));
    if (error) throw new Error(error);
  });

  ipcMain.handle("show-image-in-folder", async (event, id: unknown) => {
    assertTrustedSender(event);
    shell.showItemInFolder(await resolveImagePath(id));
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
