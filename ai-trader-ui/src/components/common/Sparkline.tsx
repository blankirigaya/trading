/** Tiny SVG trend sparkline. Pure presentation of caller-supplied points. */

export function Sparkline({
  points,
  width = 96,
  height = 28,
  positive,
  testId,
}: {
  points: number[]
  width?: number
  height?: number
  /** Force direction colour; defaults to first-vs-last point. */
  positive?: boolean
  testId?: string
}) {
  if (points.length < 2) return null
  const first = points[0] as number
  const last = points[points.length - 1] as number
  const min = Math.min(...points)
  const max = Math.max(...points)
  const span = max - min || 1
  const stepX = width / (points.length - 1)
  const path = points
    .map((value, index) => {
      const x = (index * stepX).toFixed(1)
      const y = (height - 3 - ((value - min) / span) * (height - 6)).toFixed(1)
      return `${index === 0 ? 'M' : 'L'}${x},${y}`
    })
    .join(' ')
  const up = positive ?? last >= first
  const stroke = up ? 'var(--ok)' : 'var(--bad)'
  const area = `${path} L${width.toFixed(1)},${height} L0,${height} Z`
  const id = `spark-${Math.abs(hashCode(points.join(','))) % 100000}`
  return (
    <svg
      className="spark"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      data-testid={testId}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.25" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${id})`} />
      <path d={path} fill="none" stroke={stroke} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  )
}

function hashCode(text: string): number {
  let hash = 0
  for (let i = 0; i < text.length; i += 1) {
    hash = (hash * 31 + text.charCodeAt(i)) | 0
  }
  return hash
}
