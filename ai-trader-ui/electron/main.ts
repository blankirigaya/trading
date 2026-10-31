import { app, BrowserWindow, shell } from 'electron'
import { join } from 'node:path'

const isDev = !app.isPackaged

/**
 * The renderer is sandboxed and context-isolated. It has no Node.js access and
 * cannot reach the filesystem, environment, or child processes. All backend
 * communication happens over HTTP/WS to the FastAPI service.
 */
function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1600,
    height: 1000,
    minWidth: 1200,
    minHeight: 760,
    backgroundColor: '#0b0f14',
    show: false,
    // Hide the Windows title bar: the app supplies its own header. The native
    // minimize/maximize/close buttons are kept via a caption overlay so the window
    // stays closable and resizable without adding an IPC bridge to the renderer
    // (the renderer's preload is intentionally capability-free).
    titleBarStyle: 'hidden',
    titleBarOverlay: { color: '#11161d', symbolColor: '#c8d3e0', height: 36 },
    webPreferences: {
      // Built as CommonJS (see electron.vite.config.ts) because a sandboxed preload
      // is executed as CommonJS, not as the project's default ESM.
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      // Keep the 1s price poll running while the window is in the background.
      // Chromium otherwise throttles timers in unfocused/hidden windows, which
      // made live prices freeze the moment another app was clicked.
      backgroundThrottling: false,
    },
  })

  window.once('ready-to-show', () => window.show())

  // External links open in the user's browser, never inside the app shell.
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http://') || url.startsWith('https://')) {
      void shell.openExternal(url)
    }
    return { action: 'deny' }
  })

  const devServer = process.env.ELECTRON_RENDERER_URL
  if (isDev && devServer) {
    void window.loadURL(devServer)
  } else {
    void window.loadFile(join(__dirname, '../renderer/index.html'))
  }

  return window
}

app.whenReady().then(() => {
  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
