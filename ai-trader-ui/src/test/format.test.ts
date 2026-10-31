import { formatDateTime, formatInr, formatLatency, formatPercent, formatPrice, formatSignedInr, formatTime, toNumber } from '@/utils/format'

describe('Indian currency formatting', () => {
  it('uses the Indian numbering system', () => {
    expect(formatInr(100000)).toContain('1,00,000')
    expect(formatInr(12450.3)).toContain('12,450.30')
  })

  it('formats zero and missing values safely', () => {
    expect(formatInr(0)).toContain('0.00')
    expect(formatInr(null)).toContain('0.00')
    expect(formatInr(undefined)).toContain('0.00')
    expect(toNumber('not-a-number')).toBe(0)
  })

  it('signs gains and losses', () => {
    expect(formatSignedInr(150)).toMatch(/^\+/)
    expect(formatSignedInr(-150)).toMatch(/^-/)
    expect(formatSignedInr(0)).not.toMatch(/^[+-]/)
  })

  it('formats prices, percentages and latency', () => {
    expect(formatPrice(1500.25)).toBe('1,500.25')
    expect(formatPercent(0.735, 0)).toBe('74%')
    expect(formatLatency(450)).toBe('450 ms')
    expect(formatLatency(1500)).toBe('1.50 s')
    expect(formatLatency(null)).toBe('n/a')
  })

  it('renders timestamps in IST regardless of browser locale', () => {
    // 06:46:15 UTC is 12:16:15 IST.
    expect(formatTime('2026-10-22T06:46:15+00:00')).toBe('12:16:15')
    expect(formatDateTime('2026-10-22T06:46:15+00:00')).toBe('22/10/2026 12:16:15')
    // Naive backend timestamps are UTC wall time.
    expect(formatTime('2026-10-22T06:46:15')).toBe('12:16:15')
    expect(formatTime(null)).toBe('n/a')
    expect(formatDateTime('not-a-date')).toBe('n/a')
  })
})
