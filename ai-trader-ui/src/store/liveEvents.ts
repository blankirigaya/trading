/**
 * Transient live-event buffer for the timeline.
 *
 * This is presentation state only. TanStack Query remains the source of truth for
 * portfolio, positions, and decisions; these events merely invalidate queries.
 */

import { create } from 'zustand'
import type { TimelineEvent } from '@/api/types'

const MAX_EVENTS = 500

interface LiveEventsState {
  events: TimelineEvent[]
  streamState: 'connecting' | 'open' | 'reconnecting' | 'closed'
  lastError: TimelineEvent | null
  append: (event: TimelineEvent) => void
  hydrate: (events: TimelineEvent[]) => void
  setStreamState: (state: LiveEventsState['streamState']) => void
  clearError: () => void
  reset: () => void
}

export const useLiveEvents = create<LiveEventsState>((set) => ({
  events: [],
  streamState: 'connecting',
  lastError: null,
  append: (event) =>
    set((state) => {
      const next = [...state.events, event]
      return {
        events: next.length > MAX_EVENTS ? next.slice(next.length - MAX_EVENTS) : next,
        lastError: event.category === 'ERROR' ? event : state.lastError,
      }
    }),
  hydrate: (events) => set({ events: events.slice(-MAX_EVENTS) }),
  setStreamState: (streamState) => set({ streamState }),
  clearError: () => set({ lastError: null }),
  reset: () => set({ events: [], streamState: 'connecting', lastError: null }),
}))
