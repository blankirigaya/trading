import { contextBridge } from 'electron'

/**
 * The renderer needs no privileged capability: it talks to the FastAPI backend
 * over HTTP and WebSocket from the page itself. Nothing is exposed here beyond a
 * read-only environment marker so the UI can label whether it is packaged.
 */
export interface MonitorBridge {
  readonly isPackaged: boolean
  readonly platform: string
}

const bridge: MonitorBridge = {
  isPackaged: process.env.NODE_ENV === 'production',
  platform: process.platform,
}

if (process.contextIsolated) {
  contextBridge.exposeInMainWorld('monitor', bridge)
} else {
  // contextIsolation is enforced in main; this branch should never be reached.
  ;(globalThis as unknown as { monitor: MonitorBridge }).monitor = bridge
}
