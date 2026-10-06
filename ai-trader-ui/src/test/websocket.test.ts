import { backoffDelay, EventStream, MAX_BACKOFF_MS } from '@/api/websocket'

class FakeSocket {
  static instances: FakeSocket[] = []
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  closed = false

  constructor(readonly url: string) {
    FakeSocket.instances.push(this)
  }

  close(): void {
    this.closed = true
  }
}

describe('WebSocket backoff', () => {
  it('grows exponentially and is bounded', () => {
    expect(backoffDelay(0)).toBe(500)
    expect(backoffDelay(1)).toBe(1000)
    expect(backoffDelay(2)).toBe(2000)
    expect(backoffDelay(3)).toBe(4000)
    expect(backoffDelay(20)).toBe(MAX_BACKOFF_MS)
  })
})

describe('EventStream', () => {
  beforeEach(() => {
    FakeSocket.instances = []
    vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('opens a socket to the configured events path', () => {
    const stream = new EventStream({ onEvent: vi.fn() })
    stream.connect()
    expect(FakeSocket.instances).toHaveLength(1)
    expect(FakeSocket.instances[0]!.url).toContain('/events')
  })

  it('ingests events and reports stream state', () => {
    const onEvent = vi.fn()
    const onStateChange = vi.fn()
    const stream = new EventStream({ onEvent, onStateChange })
    stream.connect()
    const socket = FakeSocket.instances[0]!

    socket.onopen?.()
    expect(onStateChange).toHaveBeenCalledWith('open')

    socket.onmessage?.({
      data: JSON.stringify({
        seq: 1,
        timestamp: '2026-09-01T04:00:00+00:00',
        category: 'AI',
        kind: 'ai_initial_decision',
        message: 'Initial AI decision',
        decision_id: null,
        correlation_id: null,
        symbol: 'INFY',
        data: {},
      }),
    } as MessageEvent<string>)
    expect(onEvent).toHaveBeenCalledTimes(1)
  })

  it('ignores malformed frames without tearing down', () => {
    const onEvent = vi.fn()
    const stream = new EventStream({ onEvent })
    stream.connect()
    const socket = FakeSocket.instances[0]!
    socket.onopen?.()
    expect(() => socket.onmessage?.({ data: 'not json' } as MessageEvent<string>)).not.toThrow()
    expect(onEvent).not.toHaveBeenCalled()
  })

  it('reconnects with bounded exponential backoff', () => {
    const stream = new EventStream({ onEvent: vi.fn() })
    stream.connect()
    expect(FakeSocket.instances).toHaveLength(1)

    // First drop schedules a 500ms retry.
    FakeSocket.instances[0]!.onclose?.()
    expect(FakeSocket.instances).toHaveLength(1)
    vi.advanceTimersByTime(500)
    expect(FakeSocket.instances).toHaveLength(2)

    // Second drop escalates to 1000ms: no retry at 500ms, one at 1000ms.
    FakeSocket.instances[1]!.onclose?.()
    vi.advanceTimersByTime(500)
    expect(FakeSocket.instances).toHaveLength(2)
    vi.advanceTimersByTime(500)
    expect(FakeSocket.instances).toHaveLength(3)
  })

  it('keeps escalating when a connection never opens', () => {
    // Regression: a socket that never reaches onopen must not reset backoff,
    // otherwise a down backend is retried forever at the base delay.
    const stream = new EventStream({ onEvent: vi.fn() })
    stream.connect()

    for (let round = 0; round < 4; round += 1) {
      FakeSocket.instances[FakeSocket.instances.length - 1]!.onclose?.()
      // Advance only half the expected delay; the retry must not have fired yet
      // once the delay has grown beyond 500ms.
      if (stream.currentDelayMs > 500) {
        vi.advanceTimersByTime(500)
        expect(FakeSocket.instances[FakeSocket.instances.length - 1]!.closed).toBe(false)
      }
    }
    expect(FakeSocket.instances.length).toBeLessThanOrEqual(2)
  })

  it('does not reconnect after an explicit close', () => {
    const stream = new EventStream({ onEvent: vi.fn() })
    stream.connect()
    stream.close()
    vi.advanceTimersByTime(30_000)
    expect(FakeSocket.instances).toHaveLength(1)
  })
})
