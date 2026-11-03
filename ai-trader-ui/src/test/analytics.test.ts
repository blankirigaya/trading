import { describe, expect, it } from 'vitest'

import type { ClosedTradeRow } from '@/api/types'
import {
  adverseSlippageBps,
  dailyPerformance,
  decisionConversion,
  dedupeClosedTrades,
  edgeStatistics,
  excursionStats,
  holdingDurations,
  maxDrawdown,
  currentDrawdown,
  realizedEquityPoints,
  streaks,
  summarizeTrades,
  symbolPerformance,
  utcDateOf,
} from '@/analytics/metrics'

let idCounter = 0

function trade(overrides: Partial<ClosedTradeRow> = {}): ClosedTradeRow {
  idCounter += 1
  return {
    closed_trade_id: `ct-${idCounter}`,
    shard: 0,
    symbol: 'RELIANCE',
    quantity: 1,
    entry_price: '100',
    exit_price: '100',
    realized_pnl: '0',
    charges: '0',
    gross_pnl: '0',
    opened_at: '2026-01-01T09:15:00+05:30',
    closed_at: '2026-01-01T15:30:00+05:30',
    entry_decision_id: null,
    exit_decision_id: 'd-1',
    ...overrides,
  }
}

describe('win rate', () => {
  it('computes winning / total completed × 100 including breakeven in the denominator', () => {
    const trades = [
      trade({ realized_pnl: '100' }),
      trade({ realized_pnl: '50' }),
      trade({ realized_pnl: '-30' }),
      trade({ realized_pnl: '0' }),
    ]
    expect(summarizeTrades(trades).winRatePct).toBe(50)
  })

  it('returns null with zero trades', () => {
    expect(summarizeTrades([]).winRatePct).toBeNull()
  })

  it('returns 100 when every trade wins', () => {
    const trades = [trade({ realized_pnl: '10' }), trade({ realized_pnl: '20' })]
    expect(summarizeTrades(trades).winRatePct).toBe(100)
  })
})

describe('profit factor', () => {
  it('is gross profits / absolute gross losses', () => {
    const trades = [
      trade({ realized_pnl: '200' }),
      trade({ realized_pnl: '100' }),
      trade({ realized_pnl: '-150' }),
    ]
    expect(summarizeTrades(trades).profitFactor).toBeCloseTo(2, 10)
  })

  it('is Infinity with profits and zero losses', () => {
    const trades = [trade({ realized_pnl: '100' }), trade({ realized_pnl: '0' })]
    expect(summarizeTrades(trades).profitFactor).toBe(Infinity)
  })

  it('is null when there are no trades (insufficient data)', () => {
    expect(summarizeTrades([]).profitFactor).toBeNull()
  })

  it('is null when all trades are breakeven (0/0 is undefined, not 0)', () => {
    const trades = [trade({ realized_pnl: '0' }), trade({ realized_pnl: '0' })]
    expect(summarizeTrades(trades).profitFactor).toBeNull()
  })
})

describe('average win and average loss', () => {
  it('averages only profitable trades for average win', () => {
    const trades = [
      trade({ realized_pnl: '300' }),
      trade({ realized_pnl: '100' }),
      trade({ realized_pnl: '-40' }),
    ]
    expect(summarizeTrades(trades).avgWin).toBe(200)
  })

  it('averages losing trades as a negative value with a positive magnitude', () => {
    const trades = [
      trade({ realized_pnl: '-100' }),
      trade({ realized_pnl: '-300' }),
      trade({ realized_pnl: '50' }),
    ]
    const summary = summarizeTrades(trades)
    expect(summary.avgLoss).toBe(-200)
    expect(summary.avgLossMagnitude).toBe(200)
  })

  it('returns null for average win with no wins and null for average loss with no losses', () => {
    const onlyWins = [trade({ realized_pnl: '10' })]
    expect(summarizeTrades(onlyWins).avgWin).toBe(10)
    expect(summarizeTrades(onlyWins).avgLoss).toBeNull()
    const onlyLosses = [trade({ realized_pnl: '-10' })]
    expect(summarizeTrades(onlyLosses).avgWin).toBeNull()
    expect(summarizeTrades(onlyLosses).avgLoss).toBe(-10)
  })
})

describe('expectancy', () => {
  it('equals (winRate × avgWin) − (lossRate × avgLossMagnitude)', () => {
    const trades = [
      trade({ realized_pnl: '200' }),
      trade({ realized_pnl: '-50' }),
      trade({ realized_pnl: '-150' }),
    ]
    const summary = summarizeTrades(trades)
    // 33.333% × 200 − 66.667% × 100
    expect(summary.expectancy).toBeCloseTo((1 / 3) * 200 - (2 / 3) * 100, 10)
  })

  it('equals total P&L / total trades (breakeven contribute zero)', () => {
    const trades = [
      trade({ realized_pnl: '100' }),
      trade({ realized_pnl: '-50' }),
      trade({ realized_pnl: '0' }),
      trade({ realized_pnl: '0' }),
    ]
    const summary = summarizeTrades(trades)
    expect(summary.expectancy).toBeCloseTo(50 / 4, 10)
  })

  it('is null with zero trades', () => {
    expect(summarizeTrades([]).expectancy).toBeNull()
  })
})

describe('maximum drawdown', () => {
  it('measures the largest peak-to-trough decline in rupees and percent', () => {
    const points = [{ equity: 100000 }, { equity: 120000 }, { equity: 90000 }, { equity: 110000 }]
    const dd = maxDrawdown(points)
    expect(dd).not.toBeNull()
    expect(dd?.amount).toBe(30000)
    // peak 120000 → trough 90000 = 25%
    expect(dd?.percent).toBeCloseTo(25, 10)
    expect(dd?.peak).toBe(120000)
    expect(dd?.trough).toBe(90000)
  })

  it('is zero when equity only rises', () => {
    const dd = maxDrawdown([{ equity: 100 }, { equity: 200 }, { equity: 300 }])
    expect(dd?.amount).toBe(0)
    expect(dd?.percent).toBe(0)
  })

  it('is null with fewer than two observations rather than an estimate', () => {
    expect(maxDrawdown([])).toBeNull()
    expect(maxDrawdown([{ equity: 100 }])).toBeNull()
  })
})

describe('current drawdown', () => {
  it('measures latest equity against the running peak', () => {
    const dd = currentDrawdown([{ equity: 100 }, { equity: 150 }, { equity: 120 }])
    expect(dd?.amount).toBe(30)
    expect(dd?.percent).toBeCloseTo(20, 10)
  })

  it('is null with a single observation', () => {
    expect(currentDrawdown([{ equity: 100 }])).toBeNull()
  })
})

describe('daily realized P&L', () => {
  it('groups completed trades by UTC calendar day of close', () => {
    const trades = [
      trade({ closed_at: '2026-01-01T15:30:00+05:30', realized_pnl: '100' }),
      trade({ closed_at: '2026-01-01T16:00:00+05:30', realized_pnl: '-40' }),
      trade({ closed_at: '2026-01-02T15:30:00+05:30', realized_pnl: '60' }),
    ]
    const days = dailyPerformance(trades)
    expect(days).toHaveLength(2)
    expect(days[0]?.date).toBe('2026-01-01')
    expect(days[0]?.pnl).toBe(60)
    expect(days[0]?.trades).toBe(2)
    expect(days[0]?.wins).toBe(1)
    expect(days[0]?.losses).toBe(1)
    expect(days[0]?.winRatePct).toBe(50)
    expect(days[0]?.avgPnlPerTrade).toBe(30)
    expect(days[1]?.date).toBe('2026-01-02')
  })

  it('does not emit days without completed trades (missing ≠ zero)', () => {
    const trades = [trade({ closed_at: '2026-01-05T10:00:00Z', realized_pnl: '10' })]
    const days = dailyPerformance(trades)
    expect(days.map((d) => d.date)).toEqual(['2026-01-05'])
  })

  it('returns an empty array with zero trades', () => {
    expect(dailyPerformance([])).toEqual([])
  })

  it('skips rows with unparseable close timestamps', () => {
    const days = dailyPerformance([trade({ closed_at: 'not-a-date' })])
    expect(days).toEqual([])
  })
})

describe('symbol-wise performance', () => {
  it('groups by symbol with per-symbol win rate, P&L, profit factor and average hold', () => {
    const trades = [
      trade({ symbol: 'RELIANCE', realized_pnl: '100' }),
      trade({ symbol: 'RELIANCE', realized_pnl: '-50' }),
      trade({ symbol: 'TCS', realized_pnl: '30' }),
    ]
    const rows = symbolPerformance(trades)
    expect(rows).toHaveLength(2)
    const reliance = rows.find((r) => r.symbol === 'RELIANCE')
    expect(reliance?.trades).toBe(2)
    expect(reliance?.wins).toBe(1)
    expect(reliance?.losses).toBe(1)
    expect(reliance?.winRatePct).toBe(50)
    expect(reliance?.totalPnl).toBe(50)
    expect(reliance?.avgPnl).toBe(25)
    expect(reliance?.profitFactor).toBe(2)
    expect(reliance?.avgHoldMs).toBeGreaterThan(0)
    const tcs = rows.find((r) => r.symbol === 'TCS')
    expect(tcs?.totalPnl).toBe(30)
    expect(tcs?.profitFactor).toBe(Infinity)
  })

  it('does not mix open positions or recommendations into symbol statistics', () => {
    const rows = symbolPerformance([trade({ symbol: 'INFY', realized_pnl: '10' })])
    expect(rows).toHaveLength(1)
    expect(rows[0]?.symbol).toBe('INFY')
  })
})

describe('slippage calculations', () => {
  it('treats BUY above reference as adverse (positive)', () => {
    expect(adverseSlippageBps('BUY', 100, 100.5)).toBeCloseTo(50, 10)
  })

  it('treats BUY below reference as favorable (negative)', () => {
    expect(adverseSlippageBps('BUY', 100, 99.5)).toBeCloseTo(-50, 10)
  })

  it('treats SELL below reference as adverse (positive)', () => {
    expect(adverseSlippageBps('SELL', 100, 99.5)).toBeCloseTo(50, 10)
  })

  it('treats SELL above reference as favorable (negative)', () => {
    expect(adverseSlippageBps('SELL', 100, 100.5)).toBeCloseTo(-50, 10)
  })

  it('is unavailable (null) when prices are missing or invalid', () => {
    expect(adverseSlippageBps('BUY', null, 100)).toBeNull()
    expect(adverseSlippageBps('BUY', 100, undefined)).toBeNull()
    expect(adverseSlippageBps('BUY', 0, 100)).toBeNull()
    expect(adverseSlippageBps('BUY', -1, 100)).toBeNull()
    expect(adverseSlippageBps('HOLD', 100, 101)).toBeNull()
    expect(adverseSlippageBps('buy', Number.NaN, 100)).toBeNull()
  })
})

describe('duplicate-fill protection', () => {
  it('removes repeated closed_trade_id rows so a fill is never counted twice', () => {
    const a = trade({ realized_pnl: '100' })
    const b = trade({ realized_pnl: '50' })
    const deduped = dedupeClosedTrades([a, b, a, b])
    expect(deduped).toHaveLength(2)
    expect(summarizeTrades(deduped).total).toBe(2)
    expect(summarizeTrades(deduped).totalPnl).toBe(150)
  })

  it('keeps rows without an id (cannot be proven duplicates)', () => {
    const noId = trade({ closed_trade_id: '', realized_pnl: '10' })
    expect(dedupeClosedTrades([noId, { ...noId }])).toHaveLength(2)
  })
})

describe('partial-fill aggregation (multiple round trips per symbol)', () => {
  it('counts each closed round trip exactly once and sums P&L without double counting', () => {
    const trades = [
      trade({ symbol: 'TCS', closed_trade_id: 'ct-a', realized_pnl: '100' }),
      trade({ symbol: 'TCS', closed_trade_id: 'ct-b', realized_pnl: '200' }),
    ]
    const summary = summarizeTrades(dedupeClosedTrades(trades))
    expect(summary.total).toBe(2)
    expect(summary.totalPnl).toBe(300)
    const symbolRows = symbolPerformance(trades)
    expect(symbolRows).toHaveLength(1)
    expect(symbolRows[0]?.totalPnl).toBe(300)
  })
})

describe('open versus closed trade separation', () => {
  it('realized statistics only ever consume closed rows; recommendations stay separate', () => {
    const closed = [trade({ realized_pnl: '100' })]
    // Two decisions, one with generated orders — recommendations are not trades.
    const conversion = decisionConversion([
      { orders_generated: [{}] },
      { orders_generated: [] },
    ])
    const summary = summarizeTrades(closed)
    expect(summary.total).toBe(1)
    expect(conversion.total).toBe(2)
    expect(conversion.executed).toBe(1)
    expect(conversion.conversionPct).toBe(50)
    // The unexecuted recommendation never enters the win rate.
    expect(summary.winRatePct).toBe(100)
  })

  it('reports conversion as null with no decisions (not 0%)', () => {
    expect(decisionConversion([]).conversionPct).toBeNull()
  })
})

describe('missing-data handling', () => {
  it('excludes trades without an entry timestamp from duration stats instead of assuming zero', () => {
    const stats = holdingDurations([
      trade({ opened_at: null }),
      trade({ opened_at: '2026-01-01T09:15:00Z', closed_at: '2026-01-01T10:15:00Z' }),
    ])
    expect(stats.count).toBe(1)
    expect(stats.avgMs).toBe(3600000)
  })

  it('returns null durations when no row has usable timestamps', () => {
    const stats = holdingDurations([trade({ opened_at: null })])
    expect(stats.count).toBe(0)
    expect(stats.avgMs).toBeNull()
    expect(stats.maxMs).toBeNull()
    expect(stats.minMs).toBeNull()
  })

  it('returns all-null summary metrics with zero trades', () => {
    const summary = summarizeTrades([])
    expect(summary.total).toBe(0)
    expect(summary.winRatePct).toBeNull()
    expect(summary.profitFactor).toBeNull()
    expect(summary.avgWin).toBeNull()
    expect(summary.avgLoss).toBeNull()
    expect(summary.expectancy).toBeNull()
    expect(summary.largestWin).toBeNull()
    expect(summary.largestLoss).toBeNull()
    expect(summary.totalPnl).toBe(0)
  })

  it('parses UTC dates defensively', () => {
    expect(utcDateOf('2026-03-05T18:30:00+05:30')).toBe('2026-03-05')
    expect(utcDateOf('garbage')).toBeNull()
  })
})

describe('zero-trade handling', () => {
  it('every aggregate is empty/null safe', () => {
    expect(dailyPerformance([])).toEqual([])
    expect(symbolPerformance([])).toEqual([])
    expect(maxDrawdown([])).toBeNull()
    expect(currentDrawdown([])).toBeNull()
    expect(streaks([])).toEqual({ maxWinStreak: 0, maxLossStreak: 0, currentStreak: 0 })
    expect(dedupeClosedTrades([])).toEqual([])
  })
})

describe('realized equity curve', () => {
  it('starts at starting capital and accumulates realized P&L in close order', () => {
    const trades = [
      trade({ closed_at: '2026-01-03T10:00:00Z', realized_pnl: '-50' }),
      trade({ closed_at: '2026-01-01T10:00:00Z', realized_pnl: '100' }),
      trade({ closed_at: '2026-01-02T10:00:00Z', realized_pnl: '25' }),
    ]
    const points = realizedEquityPoints(trades, 100000)
    expect(points.map((p) => p.equity)).toEqual([100100, 100125, 100075])
    expect(points[0]?.time).toBe('2026-01-01T10:00:00Z')
  })

  it('skips rows with unparseable close timestamps rather than misordering', () => {
    const points = realizedEquityPoints(
      [trade({ closed_at: 'not-a-date', realized_pnl: '9999' }), trade({ realized_pnl: '10' })],
      50000,
    )
    expect(points).toHaveLength(1)
    expect(points[0]?.equity).toBe(50010)
  })

  it('is empty with zero trades (callers render the unavailable state)', () => {
    expect(realizedEquityPoints([], 100000)).toEqual([])
  })
})

describe('streaks', () => {
  it('finds the longest winning and losing runs in close order', () => {
    const trades = [
      trade({ closed_at: '2026-01-01T10:00:00Z', realized_pnl: '10' }),
      trade({ closed_at: '2026-01-02T10:00:00Z', realized_pnl: '20' }),
      trade({ closed_at: '2026-01-03T10:00:00Z', realized_pnl: '-5' }),
      trade({ closed_at: '2026-01-04T10:00:00Z', realized_pnl: '-5' }),
      trade({ closed_at: '2026-01-05T10:00:00Z', realized_pnl: '-5' }),
      trade({ closed_at: '2026-01-06T10:00:00Z', realized_pnl: '10' }),
    ]
    const result = streaks(trades)
    expect(result.maxWinStreak).toBe(2)
    expect(result.maxLossStreak).toBe(3)
    expect(result.currentStreak).toBe(1)
  })

  it('a breakeven trade resets both streaks', () => {
    const trades = [
      trade({ closed_at: '2026-01-01T10:00:00Z', realized_pnl: '10' }),
      trade({ closed_at: '2026-01-02T10:00:00Z', realized_pnl: '0' }),
      trade({ closed_at: '2026-01-03T10:00:00Z', realized_pnl: '10' }),
    ]
    const result = streaks(trades)
    expect(result.maxWinStreak).toBe(1)
    expect(result.currentStreak).toBe(1)
  })
})

describe('edgeStatistics', () => {
  it('reports impossible-to-call results as not significant', () => {
    // Mean is positive but the spread is large: classic small-sample noise.
    const trades = [
      trade({ realized_pnl: '100' }),
      trade({ realized_pnl: '-90' }),
      trade({ realized_pnl: '80' }),
      trade({ realized_pnl: '-70' }),
    ]
    const stats = edgeStatistics(trades)
    expect(stats.n).toBe(4)
    expect(stats.mean).toBeCloseTo(5, 5)
    expect(stats.significantlyPositive).toBe(false)
    expect(stats.significantlyNegative).toBe(false)
    expect(stats.ci95Low).toBeLessThan(0)
    expect(stats.ci95High).toBeGreaterThan(0)
  })

  it('flags a consistently positive series as significant', () => {
    const trades = [
      trade({ realized_pnl: '21' }),
      trade({ realized_pnl: '19' }),
      trade({ realized_pnl: '20' }),
      trade({ realized_pnl: '22' }),
      trade({ realized_pnl: '18' }),
    ]
    const stats = edgeStatistics(trades)
    expect(stats.significantlyPositive).toBe(true)
    expect(stats.ci95Low).toBeGreaterThan(0)
    expect(stats.requiredSampleSize).toBeLessThanOrEqual(5)
  })

  it('returns nulls rather than zeros with no trades', () => {
    const stats = edgeStatistics([])
    expect(stats.mean).toBeNull()
    expect(stats.ci95Low).toBeNull()
    expect(stats.significantlyPositive).toBeNull()
  })
})

describe('excursionStats', () => {
  it('ignores trades recorded before excursions were measured', () => {
    const trades = [
      trade({ max_unrealized: null, min_unrealized: null }),
      trade({ max_unrealized: '50', min_unrealized: '-10', gross_pnl: '30' }),
    ]
    const stats = excursionStats(trades)
    expect(stats.n).toBe(1)
    expect(stats.avgMfe).toBe(50)
  })

  it('measures profit given back before the exit', () => {
    // Peaked at +100 but booked +40: 60 given back of 150 total peak profit
    // (second trade peaked at +50, booked all 50).
    const trades = [
      trade({ max_unrealized: '100', min_unrealized: '-20', gross_pnl: '40' }),
      trade({ max_unrealized: '50', min_unrealized: '0', gross_pnl: '50' }),
    ]
    const stats = excursionStats(trades)
    expect(stats.avgMfe).toBe(75)
    expect(stats.medianMfe).toBe(75)
    expect(stats.totalGivenBack).toBe(60)
    expect(stats.giveBackPct).toBeCloseTo((60 / 150) * 100, 5)
  })

  it('counts trades that gave back the entire profit', () => {
    const trades = [trade({ max_unrealized: '80', min_unrealized: '-5', gross_pnl: '-2' })]
    const stats = excursionStats(trades)
    expect(stats.gaveBackAll).toBe(1)
  })

  it('reports no data instead of zero when nothing was measured', () => {
    const stats = excursionStats([trade({ max_unrealized: null })])
    expect(stats.n).toBe(0)
    expect(stats.giveBackPct).toBeNull()
  })
})
