import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'

import { api } from '@/api/client'
import { EventStream, type StreamState } from '@/api/websocket'
import { useLiveEvents } from '@/store/liveEvents'

/** Event kinds that mean server state changed and cached queries are stale. */
const INVALIDATING_KINDS = new Set([
  'ai_final_decision',
  'ai_initial_decision',
  'paper_order',
  'paper_fill',
  'decision_persisted',
  'session_started',
  'session_stopped',
  'kill_switch',
  'paper_reset',
  'backend_started',
])
// market_update is intentionally excluded: the live queries already poll at
// 1-2s, and invalidating eight queries per tick event caused a refetch storm.

const INVALIDATED_QUERIES = [
  'systemStatus',
  'marketStatus',
  'marketSnapshots',
  'portfolio',
  'positions',
  'decisions',
  'trades',
  'performance',
] as const

/**
 * Owns a single WebSocket for the app and turns events into query invalidations.
 * The socket is never created per component.
 */
export function useEventStream(): StreamState {
  const queryClient = useQueryClient()
  const append = useLiveEvents((state) => state.append)
  const setStreamState = useLiveEvents((state) => state.setStreamState)
  const hydrate = useLiveEvents((state) => state.hydrate)
  const streamState = useLiveEvents((state) => state.streamState)
  const bootstrapped = useRef(false)

  useEffect(() => {
    if (bootstrapped.current) return
    bootstrapped.current = true

    const stream = new EventStream({
      onEvent: (event) => {
        append(event)
        if (INVALIDATING_KINDS.has(event.kind)) {
          for (const key of INVALIDATED_QUERIES) {
            void queryClient.invalidateQueries({ queryKey: [key] })
          }
        }
      },
      onStateChange: setStreamState,
    })

    // Seed from REST so a reload does not depend on socket memory.
    api
      .events()
      .then((events) => hydrate(events))
      .catch(() => undefined)

    stream.connect()
    return () => stream.close()
  }, [append, hydrate, queryClient, setStreamState])

  return streamState
}
