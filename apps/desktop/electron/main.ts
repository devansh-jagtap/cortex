import { app, BrowserWindow, dialog, ipcMain, net, protocol, session, shell, type IpcMainInvokeEvent } from "electron";
import { spawn, type ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import { createWriteStream, existsSync, mkdirSync, promises as fs } from "node:fs";
import { createServer, type AddressInfo } from "node:net";
import path from "node:path";
import { pathToFileURL } from "node:url";

// Two ways to run:
// - app mode (default): Electron serves the built UI (apps/web/out) itself and
//   starts, watches, and stops the Cortex engine. One command, no dev server.
// - dev mode (--dev): loads the Next.js dev server for hot reload and talks to
//   an engine you started yourself (`npm run dev` at the repo root).
const REPO = path.resolve(__dirname, "../../..");
const WEB_OUT = path.join(REPO, "apps", "web", "out");
const DEV_MODE = process.argv.includes("--dev") || !!process.env.CORTEX_WEB_URL;
const DEV_WEB_URL = process.env.CORTEX_WEB_URL ?? "http://localhost:3000";
const DEV_ENGINE_URL = "http://127.0.0.1:8000";
const APP_URL = "cortex://app/";
const SUPPORTED_IMAGE_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".webp"]);

// The UI's own origin. "standard" + "secure" give it normal web semantics
// (relative URLs, module workers, fetch) without touching file:// at all.
protocol.registerSchemesAsPrivileged([
  { scheme: "cortex", privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true } },
]);

app.setPath("userData", path.join(REPO, "storage", "electron-profile"));
if (DEV_MODE || process.env.CORTEX_DISABLE_GPU) {
  app.commandLine.appendSwitch("disable-gpu");
  app.commandLine.appendSwitch("disable-gpu-compositing");
  app.commandLine.appendSwitch("disable-gpu-sandbox");
  app.commandLine.appendSwitch("in-process-gpu");
  app.commandLine.appendSwitch("disable-software-rasterizer");
  app.disableHardwareAcceleration();
}

/** The Cortex engine (Python/FastAPI), started and supervised in app mode. */
class Engine {
  url = DEV_ENGINE_URL;
  token: string | null = null;
  private child: ChildProcess | null = null;
  private stopping = false;
  private crashes: number[] = [];
  private port = 0;

  async start(): Promise<void> {
    this.token = randomBytes(24).toString("hex");
    this.port = await freePort();
    this.url = `http://127.0.0.1:${this.port}`;
    this.spawn();
  }

  stop(): void {
    this.stopping = true;
    this.child?.kill();
  }

  private spawn(): void {
    const python = path.join(REPO, "apps", "backend", "venv", "Scripts", "python.exe");
    if (!existsSync(python)) {
      dialog.showErrorBox(
        "Cortex can't start its engine",
        `Python environment not found at ${python}.\n\nSet it up once with the steps in docs/development.md, then start Cortex again.`,
      );
      app.quit();
      return;
    }
    const logs = path.join(REPO, "storage", "logs");
    mkdirSync(logs, { recursive: true });
    const log = createWriteStream(path.join(logs, "engine.log"), { flags: "a" });
    log.write(`\n--- engine starting ${new Date().toISOString()} on port ${this.port}\n`);
    const child = spawn(
      python,
      ["-m", "uvicorn", "app.main:app", "--app-dir", "apps/backend", "--host", "127.0.0.1", "--port", String(this.port)],
      { cwd: REPO, env: { ...process.env, CORTEX_TOKEN: this.token! }, windowsHide: true },
    );
    child.stdout?.pipe(log);
    child.stderr?.pipe(log);
    child.on("exit", (code) => {
      log.write(`--- engine exited with code ${code}\n`);
      if (this.stopping) return;
      const now = Date.now();
      this.crashes = [...this.crashes.filter((t) => now - t < 60_000), now];
      if (this.crashes.length > 5) {
        dialog.showErrorBox(
          "The Cortex engine keeps stopping",
          `It stopped ${this.crashes.length} times in a minute. Details are in storage/logs/engine.log.`,
        );
        return;
      }
      setTimeout(() => this.spawn(), 2000);
    });
    this.child = child;
  }
}

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address() as AddressInfo;
      server.close(() => resolve(port));
    });
  });
}

const engine = new Engine();

function isAppUrl(url: string): boolean {
  try {
    if (DEV_MODE) return new URL(url).origin === new URL(DEV_WEB_URL).origin;
    return url.startsWith(APP_URL);
  } catch {
    return false;
  }
}

function openExternally(url: string): void {
  if (/^https?:\/\//i.test(url)) void shell.openExternal(url);
}

/** Serve apps/web/out at cortex://app/, never anything outside it. */
function serveBuiltUi(): void {
  protocol.handle("cortex", (request) => {
    let rel = decodeURIComponent(new URL(request.url).pathname);
    if (rel.endsWith("/")) rel += "index.html";
    const file = path.normalize(path.join(WEB_OUT, rel));
    if (!file.startsWith(WEB_OUT + path.sep) || !existsSync(file)) {
      return new Response("Not found", { status: 404 });
    }
    return net.fetch(pathToFileURL(file).toString());
  });
}

/** Authenticate the window's requests to the engine (the page never sees the token). */
function decorateRequests(): void {
  if (!engine.token) return;
  const token = engine.token;
  session.defaultSession.webRequest.onBeforeSendHeaders({ urls: [`${engine.url}/*`] }, (details, callback) => {
    callback({ requestHeaders: { ...details.requestHeaders, "X-Cortex-Token": token } });
  });
}

function createMainWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 720,
    minHeight: 520,
    backgroundColor: "#0e1020",
    title: "Cortex",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  });

  // The window only ever shows Cortex itself. Links to the outside world
  // open in the user's browser instead.
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

  void win.loadURL(DEV_MODE ? DEV_WEB_URL : APP_URL);
  return win;
}

function assertTrustedSender(event: IpcMainInvokeEvent): void {
  const url = event.senderFrame?.url;
  if (!url || !isAppUrl(url)) {
    throw new Error("Untrusted IPC sender");
  }
}

// The renderer only ever sends an image id. The path comes from the engine's
// index and is checked here before the OS is asked to do anything with it.
async function resolveImagePath(id: unknown): Promise<string> {
  if (typeof id !== "number" || !Number.isSafeInteger(id) || id <= 0) {
    throw new Error("Invalid image id");
  }
  const res = await fetch(`${engine.url}/images/${id}/metadata`, {
    headers: engine.token ? { "X-Cortex-Token": engine.token } : {},
  });
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
  // Read once by the preload script so the UI knows where the engine is.
  ipcMain.on("cortex:engine-url", (event) => {
    event.returnValue = engine.token ? engine.url : null;
  });

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

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    const [win] = BrowserWindow.getAllWindows();
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  app.whenReady().then(async () => {
    if (!DEV_MODE) {
      if (!existsSync(path.join(WEB_OUT, "index.html"))) {
        dialog.showErrorBox("Cortex isn't built yet", "Run `npm run app` from the project folder; it builds the UI first.");
        app.quit();
        return;
      }
      serveBuiltUi();
      await engine.start();
    }
    decorateRequests();
    registerIpcHandlers();
    createMainWindow();

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        createMainWindow();
      }
    });
  });
}

app.on("before-quit", () => engine.stop());

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
