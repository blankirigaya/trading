import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

const SRC = join(__dirname, '..')
const ELECTRON = join(SRC, '..', 'electron')

function collect(dir: string, extensions: string[]): string[] {
  const found: string[] = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      found.push(...collect(full, extensions))
    } else if (extensions.some((ext) => entry.endsWith(ext))) {
      found.push(full)
    }
  }
  return found
}

/**
 * Source-level guarantees: the UI must contain no provider credentials and no
 * order-placement calls. Everything flows through the FastAPI backend.
 */
describe('frontend source safety', () => {
  const sources = collect(SRC, ['.ts', '.tsx'])

  it('contains no provider credentials or secrets', () => {
    const forbidden = [
      'FYERS_ACCESS_TOKEN',
      'FYERS_SECRET_KEY',
      'FYERS_CLIENT_ID',
      'JEV_API_KEY',
      'AI_API_KEY',
      'LOCAL_AI_API_KEY',
      'UPSTOX_ACCESS_TOKEN',
    ]
    for (const file of sources) {
      if (file.includes('.test.')) continue
      const text = readFileSync(file, 'utf8')
      for (const secret of forbidden) {
        expect(text, `${file} references ${secret}`).not.toContain(secret)
      }
    }
  })

  it('never calls FYERS, Jev, or the AI model endpoints directly', () => {
    const providerHosts = [
      'api-t1.fyers.in',
      'socket.fyers.in',
      'api.upstox.com',
      'typesafe.ai',
      '/v1/systemone',
      '/v1/models',
    ]
    for (const file of sources) {
      if (file.includes('.test.')) continue
      const text = readFileSync(file, 'utf8')
      for (const host of providerHosts) {
        expect(text, `${file} references ${host}`).not.toContain(host)
      }
    }
  })

  it('exposes no live order controls', () => {
    const orderCalls = ['place_order', 'placeOrder', 'createOrder', 'submitOrder', 'buy(', 'sell(']
    for (const file of sources) {
      if (file.includes('.test.')) continue
      const text = readFileSync(file, 'utf8')
      for (const call of orderCalls) {
        expect(text, `${file} contains ${call}`).not.toContain(call)
      }
    }
  })

  it('exposes no live trading route in the API client', () => {
    const client = readFileSync(join(SRC, 'api', 'client.ts'), 'utf8')
    for (const route of ["'/order", "'/orders/place", "'/buy", "'/sell"]) {
      expect(client).not.toContain(route)
    }
  })

  it('keeps the electron main process locked down', () => {
    const main = readFileSync(join(ELECTRON, 'main.ts'), 'utf8')
    expect(main).toContain('contextIsolation: true')
    expect(main).toContain('nodeIntegration: false')
    expect(main).toContain('sandbox: true')
    expect(main).toContain('webSecurity: true')
  })

  it('does not grant the renderer filesystem or process access', () => {
    const preload = readFileSync(join(ELECTRON, 'preload.ts'), 'utf8')
    for (const capability of ['ipcRenderer', 'child_process', 'readFile', 'writeFile', 'exec']) {
      expect(preload).not.toContain(capability)
    }
  })
})
