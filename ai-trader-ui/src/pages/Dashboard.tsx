import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'

import { Badge, Empty, Stat } from '@/components/common/Primitives'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { EquityCurve } from '@/components/common/EquityCurve'
import { PageHeader } from '@/components/common/PageHeader'
import { Sparkline } from '@/components/common/Sparkline'
import { UniverseSwitcher } from '@/components/common/UniverseSwitcher'
import { DecisionDetail } from '@/components/decisions/DecisionDetail'
import { api } from '@/api/client'
import { LiveNewsFeed } from '@/components/news/LiveNews'
import { NewsLatencyTable } from '@/components/news/NewsLatency'
import { LiveTimeline } from '@/components/timeline/LiveTimeline'
import { dedupeClosedTrades, realizedEquityPoints } from '@/analytics/metrics'
import { useDecisions } from '@/hooks/useDecisions'
import { useChargeSchedule } from '@/hooks/useCharges'
import { useMarketSnapshots } from '@/hooks/useSystemStatus'
import { usePerformance } from '@/hooks/usePortfolio'
import { usePortfolio } from '@/hooks/usePortfolio'
import { useSystemStatus } from '@/hooks/useSystemStatus'
import { useTradeHistory } from '@/hooks/useTrades'
import { estimateExitChargesInr } from '@/utils/charges'
import {
  formatDuration,
  formatInr,
  formatInteger,
  formatPrice,
  formatSignedInr,
  formatTokens,
  formatUsd,
  pctChange,
  toNumber,
} from '@/utils/format'

/** Ticking clock for freshness labels; a frozen poll must look frozen. */
function useNow(intervalMs = 1_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(timer)
  }, [intervalMs])
  return now
}

function snapshotAgeSeconds(snapshots: { timestamp: string }[] | undefined, now: number): number | null {
  const times = (snapshots ?? [])
    .map((snapshot) => Date.parse(snapshot.timestamp))
    .filter((value) => Number.isFinite(value))
  if (!times.length) return null
  return Math.max(0, (now - Math.max(...times)) / 1000)
}

function PortfolioSummary() {
  const { data: portfolio, isLoading } = usePortfolio()
  const { data: status } = useSystemStatus()

  if (isLoading || !portfolio) return <Empty>Loading paper portfolio</Empty>

  const positions = Array.isArray(portfolio.positions) ? portfolio.positions : []
  const cash = toNumber(portfolio.cash)
  const invested = positions
    .filter((position) => position.quantity > 0)
    .reduce(
      (sum, position) => sum + toNumber(position.average_entry_price) * position.quantity,
      0,
    )
  const starting = toNumber(status?.shadow_initial_capital_inr) || cash + invested
  // Equity comes from the backend: a paper BUY only removes margin from cash, so
  // cash + unrealized understates the book by the margin still tied up.
  const total =
    portfolio.equity === undefined
      ? cash + toNumber(portfolio.unrealized_pnl)
      : toNumber(portfolio.equity)
  const today = toNumber(portfolio.realized_pnl) + toNumber(portfolio.unrealized_pnl)

  return (
    <>
      <div className="kpi-hero">
        <Stat hero label="Portfolio value" value={formatInr(total)} testId="stat-value" tone={today < 0 ? 'neg' : 'pos'} sub={`cash ${formatInr(cash)}`} />
        <Stat hero label="Total P&L" value={formatSignedInr(today)} tone={today < 0 ? 'neg' : 'pos'} testId="stat-pnl" sub={`realized ${formatSignedInr(portfolio.realized_pnl)}`} />
        <Stat hero label="Cash" value={formatInr(cash)} testId="stat-cash" />
        <Stat hero label="Invested" value={formatInr(invested)} />
      </div>
      <div className="stat-grid">
        <Stat label="Starting capital" value={formatInr(starting)} testId="stat-starting" />
        <Stat
          label="Realized P&L"
          value={formatSignedInr(portfolio.realized_pnl)}
          tone={toNumber(portfolio.realized_pnl) < 0 ? 'neg' : 'pos'}
        />
        <Stat
          label="Unrealized P&L"
          value={formatSignedInr(portfolio.unrealized_pnl)}
          tone={toNumber(portfolio.unrealized_pnl) < 0 ? 'neg' : 'pos'}
        />
        <Stat label="Open positions" value={formatInteger(positions.length)} />
      </div>
    </>
  )
}

function PortfolioEquity() {
  // Realized equity from the permanent closed-trade ledger, not journalled
  // per-shard snapshots: each decision's portfolio_after is one shard's book
  // (capital is split across shards), so plotting those against total
  // starting capital understates equity and pins the curve red.
  const { data: history } = useTradeHistory()
  const { data: status } = useSystemStatus()
  const starting = toNumber(status?.shadow_initial_capital_inr ?? '100000')
  const trades = dedupeClosedTrades(history?.closed_trades ?? [])
  if (trades.length < 2) return null
  const points = realizedEquityPoints(trades, starting)
  if (points.length < 2) return null
  return (
    <div className="card">
      <div className="card-title">
        <span>Portfolio equity</span>
        <Badge tone="neutral">Closed trades only</Badge>
      </div>
      <EquityCurve
        points={points}
        startingCapital={starting}
        basisLabel="Realized equity (closed trades)"
        testId="equity-curve"
      />
    </div>
  )
}

function OpenPositions() {
  const { data: portfolio } = usePortfolio()
  const { data: snapshots } = useMarketSnapshots()
  const { data: schedule } = useChargeSchedule()
  const rates = schedule?.enabled === true ? schedule.rates : null
  const prevPrices = useRef(new Map<string, number>())
  const priceBySymbol = new Map(
    (snapshots ?? []).map((snapshot) => [snapshot.instrument.symbol, snapshot.last_price]),
  )
  const positions = (portfolio?.positions ?? []).filter((position) => position.quantity > 0)
  return (
    <div className="card">
      <div className="card-title">
        <span>Open positions</span>
        <Badge tone="paper">PaperBroker</Badge>
      </div>
      {positions.length === 0 ? (
        <Empty>No open simulated positions</Empty>
      ) : (
        <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Side</th>
              <th className="num">Qty</th>
              <th className="num">Avg price</th>
              <th className="num">LTP</th>
              <th className="num">Value</th>
              <th className="num">Unrealized</th>
              <th className="num" title="Unrealized P&L minus estimated exit charges at LTP (same Zerodha intraday schedule as fills)">Est.&nbsp;net</th>
              <th className="num">P&amp;L %</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((position) => {
              const avg = toNumber(position.average_entry_price)
              const ltp = toNumber(priceBySymbol.get(position.symbol) ?? avg)
              const prev = prevPrices.current.get(position.symbol)
              prevPrices.current.set(position.symbol, ltp)
              const tick = prev === undefined || prev === ltp ? null : ltp > prev ? 'up' : 'down'
              const value = ltp * position.quantity
              const pnl = (ltp - avg) * position.quantity
              const pnlPct = avg ? ((ltp - avg) / avg) * 100 : 0
              const exitFee = rates ? estimateExitChargesInr(value, rates) : null
              const net = exitFee === null ? null : pnl - exitFee
              return (
                <tr key={position.symbol}>
                  <td>
                    <Link to="/positions">{position.symbol}</Link>
                  </td>
                  <td className="dim">LONG</td>
                  <td className="num">{position.quantity}</td>
                  <td className="num">{formatPrice(avg)}</td>
                  <td className="num">
                    <span key={ltp} className={tick ? `tick-${tick}` : undefined}>
                      {formatPrice(ltp)}
                    </span>
                  </td>
                  <td className="num">{formatInr(value)}</td>
                  <td className={`num ${pnl < 0 ? 'neg' : 'pos'}`}>{formatSignedInr(pnl)}</td>
                  <td className={`num ${net === null ? '' : net < 0 ? 'neg' : 'pos'}`} title={net === null ? 'Exit-charge schedule unavailable' : `Unrealized ${formatSignedInr(pnl)} minus est. exit charges ${formatInr(exitFee ?? 0)}`}>{net === null ? '—' : formatSignedInr(net)}</td>
                  <td className={`num ${pnlPct < 0 ? 'neg' : 'pos'}`}>{pnlPct.toFixed(2)}%</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        </div>
      )}
    </div>
  )
}

function MarketSnapshotStrip() {
  const { data: snapshots, isFetching } = useMarketSnapshots()
  const prevPrices = useRef(new Map<string, number>())
  const now = useNow()
  if (!snapshots?.length) {
    return (
      <div className="card">
        <div className="card-title">Market</div>
        <Empty>Waiting for FYERS market data</Empty>
      </div>
    )
  }
  const age = snapshotAgeSeconds(snapshots, now)
  const stale = age !== null && age > 90
  return (
    <div className="card">
      <div className="card-title">
        <span>
          <span className={isFetching ? 'live-dot fetching' : 'live-dot'} />
          Market &mdash; real FYERS prices
        </span>
        <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <Badge tone={stale ? 'warn' : 'ok'} testId="market-freshness">
            {stale ? `STALE · ${formatDuration(age)}` : `LIVE · ${formatDuration(age)}`}
          </Badge>
          <Badge tone="neutral">Market data only</Badge>
        </span>
      </div>
      <div className="stat-grid">
        {snapshots.map((snapshot) => {
          const change = pctChange(snapshot.last_price, snapshot.previous_close)
          const price = toNumber(snapshot.last_price)
          const prev = prevPrices.current.get(snapshot.instrument.symbol)
          prevPrices.current.set(snapshot.instrument.symbol, price)
          const tick = prev === undefined || prev === price ? null : price > prev ? 'up' : 'down'
          const closes = (snapshot.recent_1m_candles ?? [])
            .map((candle) => toNumber(candle.close))
            .filter((value) => Number.isFinite(value))
            .slice(-40)
          return (
            <Stat
              key={snapshot.instrument.symbol}
              label={`${snapshot.instrument.exchange} ${snapshot.instrument.symbol}`}
              value={
                <>
                  <span key={price} className={tick ? `tick-${tick}` : undefined}>
                    {formatPrice(snapshot.last_price)}
                  </span>{' '}
                  <span className={change !== null && change < 0 ? 'neg' : 'pos'}>
                    {change === null ? '' : `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`}
                  </span>
                  {closes.length > 1 ? (
                    <>
                      <br />
                      <Sparkline points={closes} />
                    </>
                  ) : null}
                </>
              }
            />
          )
        })}
      </div>
    </div>
  )
}

/**
 * Live news activity, straight from the event stream. Read-only: this panel reports
 * what the news layer and the AI did, and offers no control over either.
 */
function LiveNewsPanel() {
  return (
    <div className="two-col">
      <LiveNewsFeed limit={15} />
      <NewsLatencyTable limit={8} />
    </div>
  )
}

function AiAnalysis() {
  const { data } = usePerformance()
  if (!data) return null
  return (
    <div className="card">
      <div className="card-title">AI decision analysis</div>
      <div className="stat-grid">
        <Stat label="Total decisions" value={data.total_decisions} />
        <Stat label="Executed" value={data.executed} />
        <Stat label="Jev approvals" value={data.jev_approvals} />
        <Stat label="Jev challenges" value={data.jev_challenges} />
        <Stat label="AI changed after Jev" value={data.ai_changed_after_jev} />
        <Stat label="AI retained after Jev" value={data.ai_retained_after_jev} />
      </div>
    </div>
  )
}

function AiUsage() {
  const { data } = usePerformance()
  const usage = data?.ai_usage
  if (!usage) return null
  const totalTokens = toNumber(usage.input_tokens) + toNumber(usage.output_tokens)
  const sources: Array<[string, { calls?: number; input_tokens?: number; output_tokens?: number }]> = [
    ['Reviews', usage.sources.reviews ?? { calls: 0, input_tokens: 0, output_tokens: 0 }],
    ['Reconsiderations', usage.sources.reconsiderations ?? { calls: 0, input_tokens: 0, output_tokens: 0 }],
    ['News analysis', usage.sources.news ?? { calls: 0, input_tokens: 0, output_tokens: 0 }],
  ]
  return (
    <div className="card">
      <div className="card-title">
        <span>AI usage &amp; cost</span>
        <Badge tone="neutral">Bedrock metered</Badge>
      </div>
      <div className="stat-grid">
        <Stat label="AI calls" value={formatInteger(usage.calls)} testId="stat-ai-calls" />
        <Stat label="Input tokens" value={formatTokens(usage.input_tokens)} testId="stat-ai-input" />
        <Stat label="Output tokens" value={formatTokens(usage.output_tokens)} testId="stat-ai-output" />
        <Stat label="Total tokens" value={formatTokens(totalTokens)} testId="stat-ai-total" />
        <Stat
          label="Est. cost"
          value={usage.cost_configured && usage.estimated_cost_usd !== null ? formatUsd(usage.estimated_cost_usd) : 'set rates'}
          testId="stat-ai-cost"
        />
        <Stat
          label="Last 24h tokens"
          value={formatTokens(toNumber(usage.last_24h?.input_tokens) + toNumber(usage.last_24h?.output_tokens))}
        />
      </div>
      <table>
        <thead>
          <tr>
            <th>Source</th>
            <th className="num">Calls</th>
            <th className="num">Tokens</th>
          </tr>
        </thead>
        <tbody>
          {sources.map(([name, bucket]) => (
            <tr key={name}>
              <td className="dim">{name}</td>
              <td className="num">{formatInteger(bucket.calls)}</td>
              <td className="num">{formatTokens(toNumber(bucket.input_tokens) + toNumber(bucket.output_tokens))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Dashboard() {
  const { data: decisions } = useDecisions()
  const latest = decisions?.[0]
  const queryClient = useQueryClient()
  const [confirmingReview, setConfirmingReview] = useState(false)
  const [reviewState, setReviewState] = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [reviewError, setReviewError] = useState<string | null>(null)

  const requestReview = async () => {
    setConfirmingReview(false)
    setReviewState('running')
    setReviewError(null)
    try {
      const result = await api.requestReview()
      await queryClient.invalidateQueries({ queryKey: ['decisions'] })
      setReviewState(result.requested ? 'done' : 'idle')
    } catch (err) {
      setReviewState('error')
      setReviewError(err instanceof Error ? err.message : 'review request failed')
    }
  }

  return (
    <div>
      <PageHeader
        eyebrow="Overview"
        title="Trading desk"
        sub="Paper portfolio, live market, and the latest AI reasoning — all simulated funds."
      />
      <PortfolioSummary />
      <PortfolioEquity />
      <UniverseSwitcher />
      <div className="two-col">
        <div>
          <OpenPositions />
          <MarketSnapshotStrip />
        </div>
        <div>
          <div className="card">
            <div className="card-title">
              <span>Latest AI decision</span>
              <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <button
                  type="button"
                  className="btn"
                  data-testid="request-review"
                  disabled={reviewState === 'running'}
                  onClick={() => setConfirmingReview(true)}
                  title="Force a full AI review now across all shards with live market data"
                >
                  {reviewState === 'running' ? 'Reviewing…' : 'Request review'}
                </button>
                {latest ? (
                  <Link to="/decisions" className="dim">
                    View all
                  </Link>
                ) : null}
              </span>
            </div>
            {reviewState === 'done' ? (
              <div className="faint" data-testid="review-requested">
                Review requested — the latest decision below refreshes when it lands.
              </div>
            ) : null}
            {reviewState === 'error' ? (
              <div className="neg" data-testid="review-error">
                Review failed: {reviewError ?? 'unknown error'}
              </div>
            ) : null}
            {confirmingReview ? (
              <ConfirmDialog
                title="Request an AI review now?"
                body="Runs a full review immediately across all shards on live market data, outside the regular cadence. This spends one review cycle and can place paper orders."
                confirmLabel="Run review"
                onConfirm={() => void requestReview()}
                onCancel={() => setConfirmingReview(false)}
                testId="confirm-review"
              />
            ) : null}
            {latest ? (
              <DecisionDetail record={latest} />
            ) : (
              <Empty>No AI decision recorded yet. The AI reviews on its own cadence.</Empty>
            )}
          </div>
        </div>
      </div>
      <AiAnalysis />
      <AiUsage />
      <LiveNewsPanel />
      <LiveTimeline limit={25} compact />
    </div>
  )
}
