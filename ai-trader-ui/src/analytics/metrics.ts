/**
 * Centralized quantitative trading analytics.
 *
 * DATA INTEGRITY RULES
 * - Every function computes only from recorded rows (closed trades, decisions, equity points).
 * - `null` means "cannot be computed from available data" — callers must render
 *   "Insufficient historical data" instead of substituting a fabricated value.
 * - Open positions, unexecuted AI recommendations and mark-to-market values are
 *   never folded into realized statistics.
 *
 * SIGN / CLASSIFICATION CONVENTIONS
 * - A completed trade with realized_pnl > 0 is a win, < 0 is a loss, == 0 is breakeven.
 * - Win rate denominator is all completed trades (breakeven included), per spec:
 *   winRate = winning trades / total completed trades × 100.
 * - Expectancy uses wins/total and losses/total rates so breakeven trades contribute
 *   exactly zero (identity: expectancy == totalPnl / total).
 * - Durations require both opened_at and closed_at; rows missing either are excluded
 *   from duration statistics rather than assumed to be zero.
 * - Daily buckets are UTC calendar days derived from closed_at (deterministic, no
 *   local-timezone drift). Dates with no completed trades are omitted, never zero-filled.
 */

import type { ClosedTradeRow } from '@/api/types'
import { toNumber } from '@/utils/format'

/**
 * Drop duplicate fills defensively: the ledger is the source of truth for
 * round trips, but if the same `closed_trade_id` is ever delivered twice it
 * must not be counted twice. First occurrence wins; rows without an id are kept
 * as-is (they cannot be proven duplicates).
 */
export function dedupeClosedTrades(trades: ClosedTradeRow[]): ClosedTradeRow[] {
  const seen = new Set<string>()
  const result: ClosedTradeRow[] = []
  for (const trade of trades) {
    const id = trade.closed_trade_id
    if (id) {
      if (seen.has(id)) continue
      seen.add(id)
    }
    result.push(trade)
  }
  return result
}

export interface TradeSummary {
  total: number
  wins: number
  losses: number
  breakeven: number
  /** Percentage 0–100; null when there are no completed trades. */
  winRatePct: number | null
  grossProfit: number
  /** Absolute sum of losing P&L. */
  grossLoss: number
  /** Gross profit / gross loss. Infinity when there are profits but zero losses; null with no data or all-breakeven. */
  profitFactor: number | null
  /** Mean P&L of winning trades; null when there are no wins. */
  avgWin: number | null
  /** Mean P&L of losing trades (negative); null when there are no losses. */
  avgLoss: number | null
  /** Mean magnitude of losing trades (positive); null when there are no losses. */
  avgLossMagnitude: number | null
  /** (winRate × avgWin) − (lossRate × avgLossMagnitude) per completed trade; null with no data. */
  expectancy: number | null
  largestWin: number | null
  largestLoss: number | null
  /** Net realized P&L across all completed trades (already charges-net). */
  totalPnl: number
}

/** Aggregate realized statistics over completed (closed) trades only. */
export function summarizeTrades(trades: ClosedTradeRow[]): TradeSummary {
  const total = trades.length
  if (total === 0) {
    return {
      total: 0,
      wins: 0,
      losses: 0,
      breakeven: 0,
      winRatePct: null,
      grossProfit: 0,
      grossLoss: 0,
      profitFactor: null,
      avgWin: null,
      avgLoss: null,
      avgLossMagnitude: null,
      expectancy: null,
      largestWin: null,
      largestLoss: null,
      totalPnl: 0,
    }
  }

  let wins = 0
  let losses = 0
  let breakeven = 0
  let grossProfit = 0
  let grossLossAbs = 0
  let totalPnl = 0
  let largestWin: number | null = null
  let largestLoss: number | null = null

  for (const trade of trades) {
    const pnl = toNumber(trade.realized_pnl)
    totalPnl += pnl
    if (pnl > 0) {
      wins += 1
      grossProfit += pnl
      largestWin = largestWin === null ? pnl : Math.max(largestWin, pnl)
    } else if (pnl < 0) {
      losses += 1
      grossLossAbs += -pnl
      largestLoss = largestLoss === null ? pnl : Math.min(largestLoss, pnl)
    } else {
      breakeven += 1
    }
  }

  const winRatePct = (wins / total) * 100
  const lossRatePct = (losses / total) * 100
  const profitFactor =
    grossLossAbs > 0 ? grossProfit / grossLossAbs : grossProfit > 0 ? Infinity : null
  const avgWin = wins > 0 ? grossProfit / wins : null
  const avgLoss = losses > 0 ? -grossLossAbs / losses : null
  const avgLossMagnitude = losses > 0 ? grossLossAbs / losses : null
  const expectancy =
    avgWin !== null || avgLossMagnitude !== null
      ? (winRatePct / 100) * (avgWin ?? 0) - (lossRatePct / 100) * (avgLossMagnitude ?? 0)
      : null

  return {
    total,
    wins,
    losses,
    breakeven,
    winRatePct,
    grossProfit,
    grossLoss: grossLossAbs,
    profitFactor,
    avgWin,
    avgLoss,
    avgLossMagnitude,
    expectancy,
    largestWin,
    largestLoss,
    totalPnl,
  }
}

/**
 * Is the observed edge distinguishable from luck?
 *
 * With a handful of trades almost any expectancy looks impressive or terrible
 * by accident. This computes the standard error of the per-trade mean and a
 * two-sided 95% interval, so the UI can say "not yet distinguishable from zero"
 * instead of implying statistical proof.
 */
export interface EdgeStatistics {
  n: number
  /** Mean net P&L per completed trade. */
  mean: number | null
  /** Sample standard deviation of per-trade P&L. */
  stdDev: number | null
  standardError: number | null
  /** mean / standardError; null when n < 2 or SE is zero. */
  tStatistic: number | null
  ci95Low: number | null
  ci95High: number | null
  /** True only when the whole 95% interval is above zero. */
  significantlyPositive: boolean | null
  /** True only when the whole 95% interval is below zero. */
  significantlyNegative: boolean | null
  /** Trades needed for the observed mean to clear zero at 95%; null when no mean. */
  requiredSampleSize: number | null
}

/** Two-sided 95% critical values; falls back to 1.96 beyond 30 degrees of freedom. */
const T_CRITICAL_95: Record<number, number> = {
  1: 12.706,
  2: 4.303,
  3: 3.182,
  4: 2.776,
  5: 2.571,
  6: 2.447,
  7: 2.365,
  8: 2.306,
  9: 2.262,
  10: 2.228,
  15: 2.131,
  20: 2.086,
  25: 2.06,
  30: 2.042,
}

function tCritical(df: number): number {
  const direct = T_CRITICAL_95[df]
  if (direct !== undefined) return direct
  const keys = Object.keys(T_CRITICAL_95)
    .map(Number)
    .sort((a, b) => a - b)
  for (const key of keys) {
    if (df <= key) return T_CRITICAL_95[key] ?? 1.96
  }
  return 1.96
}

export function edgeStatistics(trades: ClosedTradeRow[]): EdgeStatistics {
  const n = trades.length
  const empty: EdgeStatistics = {
    n,
    mean: null,
    stdDev: null,
    standardError: null,
    tStatistic: null,
    ci95Low: null,
    ci95High: null,
    significantlyPositive: null,
    significantlyNegative: null,
    requiredSampleSize: null,
  }
  if (n === 0) return empty

  const values = trades.map((trade) => toNumber(trade.realized_pnl))
  const mean = values.reduce((sum, value) => sum + value, 0) / n
  if (n < 2) return { ...empty, mean }

  const variance =
    values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (n - 1)
  const stdDev = Math.sqrt(variance)
  const standardError = stdDev / Math.sqrt(n)
  if (standardError === 0) {
    // Every trade identical: the interval collapses onto the mean.
    return {
      ...empty,
      mean,
      stdDev,
      standardError,
      tStatistic: null,
      ci95Low: mean,
      ci95High: mean,
      significantlyPositive: mean > 0,
      significantlyNegative: mean < 0,
      requiredSampleSize: null,
    }
  }

  const critical = tCritical(n - 1)
  const halfWidth = critical * standardError
  const ci95Low = mean - halfWidth
  const ci95High = mean + halfWidth
  return {
    n,
    mean,
    stdDev,
    standardError,
    tStatistic: mean / standardError,
    ci95Low,
    ci95High,
    significantlyPositive: ci95Low > 0,
    significantlyNegative: ci95High < 0,
    // Trades needed for the current effect size to clear zero at 95%.
    requiredSampleSize:
      mean === 0 ? null : Math.ceil(((critical * stdDev) / Math.abs(mean)) ** 2),
  }
}

/**
 * Excursion statistics: how much unrealized profit positions reached, and how
 * much of it was given back before the exit.
 *
 * This is the honest answer to "are we exiting too late?". Only trades with a
 * recorded max_unrealized are counted; rows from before excursion tracking are
 * excluded rather than treated as flat.
 */
export interface ExcursionStats {
  /** Trades carrying excursion data. */
  n: number
  avgMfe: number | null
  medianMfe: number | null
  avgMae: number | null
  /** Sum of (max_unrealized − gross_pnl) across trades that ever went positive. */
  totalGivenBack: number | null
  /** Percentage of peak unrealized profit that was not realised. */
  giveBackPct: number | null
  /** Trades that were in profit at some point but closed at or below zero. */
  gaveBackAll: number
}

export function excursionStats(trades: ClosedTradeRow[]): ExcursionStats {
  const measured = trades.filter(
    (trade) => trade.max_unrealized !== null && trade.max_unrealized !== undefined,
  )
  if (measured.length === 0) {
    return {
      n: 0,
      avgMfe: null,
      medianMfe: null,
      avgMae: null,
      totalGivenBack: null,
      giveBackPct: null,
      gaveBackAll: 0,
    }
  }

  const mfes = measured.map((trade) => toNumber(trade.max_unrealized))
  const maes = measured.map((trade) => toNumber(trade.min_unrealized ?? 0))
  const sortedMfe = [...mfes].sort((a, b) => a - b)
  const mid = Math.floor(sortedMfe.length / 2)
  const medianMfe =
    sortedMfe.length % 2 === 0
      ? ((sortedMfe[mid - 1] ?? 0) + (sortedMfe[mid] ?? 0)) / 2
      : (sortedMfe[mid] ?? 0)

  let peakProfit = 0
  let givenBack = 0
  let gaveBackAll = 0
  measured.forEach((trade, index) => {
    const mfe = mfes[index] ?? 0
    const gross = toNumber(trade.gross_pnl)
    if (mfe <= 0) return
    peakProfit += mfe
    const shortfall = mfe - gross
    if (shortfall > 0) givenBack += shortfall
    if (gross <= 0) gaveBackAll += 1
  })

  return {
    n: measured.length,
    avgMfe: mfes.reduce((sum, value) => sum + value, 0) / measured.length,
    medianMfe,
    avgMae: maes.reduce((sum, value) => sum + value, 0) / measured.length,
    totalGivenBack: peakProfit > 0 ? givenBack : null,
    giveBackPct: peakProfit > 0 ? (givenBack / peakProfit) * 100 : null,
    gaveBackAll,
  }
}

export interface DrawdownResult {
  /** Largest peak-to-trough decline in rupees (positive number). */
  amount: number
  /** Decline as a percentage of the peak equity (positive number). */
  percent: number | null
  /** Peak equity value before the worst trough. */
  peak: number
  trough: number}

/**
 * Maximum drawdown over a chronological equity series.
 * Returns null with fewer than two observations — never estimates.
 */
export function maxDrawdown(points: { equity: number }[]): DrawdownResult | null {
  if (points.length < 2) return null
  let peak = points[0]?.equity ?? 0
  let worst: DrawdownResult | null = null
  for (const point of points) {
    if (!Number.isFinite(point.equity)) continue
    if (point.equity > peak) peak = point.equity
    const drop = peak - point.equity
    if (worst === null || drop > worst.amount) {
      worst = {
        amount: drop,
        percent: peak > 0 ? (drop / peak) * 100 : null,
        peak,
        trough: point.equity,
      }
    }
  }
  return worst
}

/**
 * Current drawdown: distance from the running peak to the latest equity value.
 * Null with fewer than two observations.
 */
export function currentDrawdown(points: { equity: number }[]): DrawdownResult | null {
  if (points.length < 2) return null
  const valid = points.filter((p) => Number.isFinite(p.equity))
  if (valid.length < 2) return null
  let peak = valid[0]?.equity ?? 0
  for (const point of valid) {
    if (point.equity > peak) peak = point.equity
  }
  const last = valid[valid.length - 1]?.equity ?? 0
  const drop = peak - last
  return {
    amount: drop,
    percent: peak > 0 ? (drop / peak) * 100 : null,
    peak,
    trough: last,
  }
}

export interface DailyPerformance {
  /** YYYY-MM-DD (UTC calendar day of closed_at). */
  date: string
  pnl: number
  trades: number
  wins: number
  losses: number
  /** null when the bucket has no trades (should not occur — empty days are omitted). */
  winRatePct: number | null
  avgPnlPerTrade: number | null
}

/**
 * Group completed trades into UTC calendar days of their close.
 * Days without completed trades are NOT emitted — missing data is not zero performance.
 */
export function dailyPerformance(trades: ClosedTradeRow[]): DailyPerformance[] {
  const buckets = new Map<string, DailyPerformance>()
  for (const trade of trades) {
    if (!trade.closed_at) continue
    const date = utcDateOf(trade.closed_at)
    if (date === null) continue
    let bucket = buckets.get(date)
    if (bucket === undefined) {
      bucket = { date, pnl: 0, trades: 0, wins: 0, losses: 0, winRatePct: null, avgPnlPerTrade: null }
      buckets.set(date, bucket)
    }
    const pnl = toNumber(trade.realized_pnl)
    bucket.pnl += pnl
    bucket.trades += 1
    if (pnl > 0) bucket.wins += 1
    else if (pnl < 0) bucket.losses += 1
  }
  const result = [...buckets.values()]
  for (const bucket of result) {
    bucket.winRatePct = bucket.trades > 0 ? (bucket.wins / bucket.trades) * 100 : null
    bucket.avgPnlPerTrade = bucket.trades > 0 ? bucket.pnl / bucket.trades : null
  }
  result.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0))
  return result
}

export interface SymbolPerformance {
  symbol: string
  trades: number
  wins: number
  losses: number
  breakeven: number
  winRatePct: number | null
  totalPnl: number
  avgPnl: number | null
  profitFactor: number | null
  /** Mean holding duration in ms; null when no trade for the symbol has an opened_at. */
  avgHoldMs: number | null
}

/** Per-symbol realized breakdown over completed trades only. */
export function symbolPerformance(trades: ClosedTradeRow[]): SymbolPerformance[] {
  const groups = new Map<string, ClosedTradeRow[]>()
  for (const trade of trades) {
    const list = groups.get(trade.symbol)
    if (list === undefined) groups.set(trade.symbol, [trade])
    else list.push(trade)
  }

  const rows: SymbolPerformance[] = []
  for (const [symbol, list] of groups) {
    const summary = summarizeTrades(list)
    const durations = holdingDurations(list)
    rows.push({
      symbol,
      trades: summary.total,
      wins: summary.wins,
      losses: summary.losses,
      breakeven: summary.breakeven,
      winRatePct: summary.winRatePct,
      totalPnl: summary.totalPnl,
      avgPnl: summary.total > 0 ? summary.totalPnl / summary.total : null,
      profitFactor: summary.profitFactor,
      avgHoldMs: durations.avgMs,
    })
  }
  return rows
}

export interface HoldingStats {
  /** Trades that have both opened_at and closed_at. */
  count: number
  avgMs: number | null
  maxMs: number | null
  minMs: number | null
}

/** Holding-duration statistics; rows missing entry or exit timestamps are excluded, not zeroed. */
export function holdingDurations(trades: ClosedTradeRow[]): HoldingStats {
  let count = 0
  let sum = 0
  let max: number | null = null
  let min: number | null = null
  for (const trade of trades) {
    if (!trade.opened_at || !trade.closed_at) continue
    const opened = Date.parse(trade.opened_at)
    const closed = Date.parse(trade.closed_at)
    if (!Number.isFinite(opened) || !Number.isFinite(closed)) continue
    const duration = closed - opened
    if (duration < 0) continue
    count += 1
    sum += duration
    max = max === null ? duration : Math.max(max, duration)
    min = min === null ? duration : Math.min(min, duration)
  }
  return {
    count,
    avgMs: count > 0 ? sum / count : null,
    maxMs: max,
    minMs: min,
  }
}

export interface StreakResult {
  /** Longest run of consecutive winning trades. */
  maxWinStreak: number
  /** Longest run of consecutive losing trades. */
  maxLossStreak: number
  /** Current run at the most recent trade: positive = wins, negative = losses, 0 = none/breakeven. */
  currentStreak: number
}

/**
 * Consecutive win/loss streaks in the order the trades closed.
 * Breakeven trades reset both streaks (they are neither wins nor losses).
 */
export function streaks(trades: ClosedTradeRow[]): StreakResult {
  const ordered = [...trades].sort((a, b) => {
    const left = Date.parse(a.closed_at)
    const right = Date.parse(b.closed_at)
    return left - right
  })
  let maxWin = 0
  let maxLoss = 0
  let runWin = 0
  let runLoss = 0
  let current = 0
  for (const trade of ordered) {
    const pnl = toNumber(trade.realized_pnl)
    if (pnl > 0) {
      runWin = runWin + 1
      runLoss = 0
      current = runWin
      maxWin = Math.max(maxWin, runWin)
    } else if (pnl < 0) {
      runLoss = runLoss + 1
      runWin = 0
      current = -runLoss
      maxLoss = Math.max(maxLoss, runLoss)
    } else {
      runWin = 0
      runLoss = 0
      current = 0
    }
  }
  return { maxWinStreak: maxWin, maxLossStreak: maxLoss, currentStreak: current }
}

export interface DecisionConversion {
  total: number
  executed: number
  unexecuted: number
  /** Executed / total × 100; null when no decisions are recorded. */
  conversionPct: number | null
}

/**
 * Recommendation-to-execution conversion using only the record-established link
 * (a decision's own `orders_generated`). Decisions without that link are never
 * attributed to an execution elsewhere, and no price-implied success is inferred.
 */
export function decisionConversion(
  decisions: { orders_generated: readonly unknown[] }[],
): DecisionConversion {
  const total = decisions.length
  if (total === 0) {
    return { total: 0, executed: 0, unexecuted: 0, conversionPct: null }
  }
  let executed = 0
  for (const decision of decisions) {
    if (decision.orders_generated.length > 0) executed += 1
  }
  return {
    total,
    executed,
    unexecuted: total - executed,
    conversionPct: (executed / total) * 100,
  }
}

/**
 * Execution slippage in basis points with an ADVERSE-POSITIVE sign convention:
 * - BUY:  execution above reference  → positive (adverse), below → negative (favorable).
 * - SELL: execution below reference   → positive (adverse), above → negative (favorable).
 *
 * Returns null when the side is not BUY/SELL or any price is missing/non-positive —
 * the metric is then reported as unavailable rather than estimated.
 */
export function adverseSlippageBps(
  side: string,
  referencePrice: number | null | undefined,
  executionPrice: number | null | undefined,
): number | null {
  const upper = side.trim().toUpperCase()
  if (upper !== 'BUY' && upper !== 'SELL') return null
  if (referencePrice === null || referencePrice === undefined || !Number.isFinite(referencePrice)) return null
  if (executionPrice === null || executionPrice === undefined || !Number.isFinite(executionPrice)) return null
  if (referencePrice <= 0 || executionPrice <= 0) return null
  const relative =
    upper === 'BUY'
      ? (executionPrice - referencePrice) / referencePrice
      : (referencePrice - executionPrice) / referencePrice
  return relative * 10000
}

/** UTC calendar day (YYYY-MM-DD) of an ISO timestamp; null when unparseable. */
export function utcDateOf(iso: string): string | null {
  const parsed = Date.parse(iso)
  if (!Number.isFinite(parsed)) return null
  return new Date(parsed).toISOString().slice(0, 10)
}

export interface RealizedEquityPoint {
  /** ISO timestamp of the close that produced this point. */
  time: string
  equity: number
}

/**
 * Combined realized-equity series: starting capital plus cumulative realized
 * P&L in close order. This is the correct basis for portfolio-level curves:
 * journalled portfolio_before/after snapshots are PER-SHARD books (starting
 * capital is split across review shards), so plotting them against total
 * starting capital understates equity and pins the curve red. The
 * closed-trade ledger is permanent and combined. Open-position (unrealized)
 * value is NOT included — callers must label the curve "realized".
 */
export function realizedEquityPoints(
  trades: ClosedTradeRow[],
  startingCapital: number,
): RealizedEquityPoint[] {
  const ordered = [...trades]
    .filter((t) => Number.isFinite(Date.parse(t.closed_at)))
    .sort((a, b) => Date.parse(a.closed_at) - Date.parse(b.closed_at))
  let running = startingCapital
  return ordered.map((t) => {
    running += toNumber(t.realized_pnl)
    return { time: t.closed_at, equity: running }
  })
}
