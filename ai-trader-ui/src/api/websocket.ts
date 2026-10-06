/**
 * WebSocket event stream with bounded exponential backoff.
 *
 * Reconnect delay grows to a hard ceiling and resets only after a stable
 * connection, so a down backend cannot create an uncontrolled reconnect loop.
 */

import type { TimelineEvent } from './types'

const WS_BASE = (import.meta.env.VITE_WS_BASE_URL as string | undefined) ?? 'ws://127.0.0.1:8000'

export const MAX_BACKOFF_MS = 30_000
export const BASE_BACKOFF_MS = 500
const STABLE_CONNECTION_MS = 10_000

export function backoffDelay(attempt: number): number {
  const delay = BASE_BACKOFF_MS * 2 ** Math.max(0, attempt)
  return Math.min(delay, MAX_BACKOFF_MS)
}

export type StreamState = 'connecting' | 'open' | 'reconnecting' | 'closed'

export interface EventStreamHandlers {
  onEvent: (event: TimelineEvent) => void
  onStateChange?: (state: StreamState) => void
}

export class EventStream {
  private socket: WebSocket | null = null
  private attempt = 0
  private timer: ReturnType<typeof setTimeout> | null = null
  private openedAt = 0
  private didOpen = false
  private closedByUser = false

  constructor(private readonly handlers: EventStreamHandlers) {}

  connect(): void {
    this.closedByUser = false
    this.open()
  }

  close(): void {
    this.closedByUser = true
    this.clearTimer()
    if (this.socket) {
      this.socket.onclose = null
      this.socket.close()
      this.socket = null
    }
    this.handlers.onStateChange?.('closed')
  }

  /** Exposed for tests: the current reconnect delay. */
  get currentDelayMs(): number {
    return backoffDelay(this.attempt)
  }

  private open(): void {
    this.handlers.onStateChange?.(this.attempt === 0 ? 'connecting' : 'reconnecting')
    let socket: WebSocket
    try {
      socket = new WebSocket(`${WS_BASE}/events`)
    } catch {
      this.scheduleReconnect()
      return
    }
    this.socket = socket
    this.didOpen = false

    socket.onopen = () => {
      this.openedAt = Date.now()
      this.didOpen = true
      this.handlers.onStateChange?.('open')
    }

    socket.onmessage = (message: MessageEvent<string>) => {
      try {
        this.handlers.onEvent(JSON.parse(message.data) as TimelineEvent)
      } catch {
        // A malformed frame must not tear down the stream.
      }
    }

    socket.onerror = () => {
      // onclose always follows; reconnect is handled there.
    }

    socket.onclose = () => {
      this.socket = null
      if (this.closedByUser) {
        this.handlers.onStateChange?.('closed')
        return
      }
      // Reset backoff only after a connection that actually opened and stayed up.
      // A socket that never opened must keep escalating, otherwise a down backend
      // would be retried at the base delay forever.
      const wasStable = this.didOpen && Date.now() - this.openedAt > STABLE_CONNECTION_MS
      this.didOpen = false
      if (wasStable) this.attempt = 0
      this.handlers.onStateChange?.('reconnecting')
      this.scheduleReconnect()
    }
  }

  private scheduleReconnect(): void {
    this.clearTimer()
    const delay = backoffDelay(this.attempt)
    this.attempt += 1
    this.timer = setTimeout(() => this.open(), delay)
  }

  private clearTimer(): void {
    if (this.timer) {
      clearTimeout(this.timer)
      this.timer = null
    }
  }
}
