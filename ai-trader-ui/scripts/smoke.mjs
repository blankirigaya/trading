/**
 * Electron startup smoke test.
 *
 * Launches the built app against a running backend, waits for the renderer to
 * report readiness, captures a screenshot, then exits. Verifies the shell really
 * renders rather than just that the bundle compiled.
 *
 * Usage: node scripts/smoke.mjs [outDir]
 */
import { spawn } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const here = fileURLToPath(new URL('.', import.meta.url))
const root = resolve(here, '..')
const outDir = resolve(root, process.argv[2] ?? 'out')
mkdirSync(join(root, 'smoke'), { recursive: true })

const electron = require('electron')

// Drive the main process through a temporary entry that loads the real build.
const harness = join(root, 'smoke', 'harness.cjs')
writeFileSync(
  harness,
  `
const { app, BrowserWindow } = require('electron')
const { join } = require('node:path')
app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1600, height: 1000, show: false,
    webPreferences: {
      preload: join(${JSON.stringify(outDir)}, 'preload/index.mjs'),
      contextIsolation: true, nodeIntegration: false, sandbox: true,
    },
  })
  await win.loadFile(join(${JSON.stringify(outDir)}, 'renderer/index.html'))
  await new Promise((r) => setTimeout(r, 4000))
  const probe = await win.webContents.executeJavaScript(\`
    (() => {
      const text = document.body.innerText
      return JSON.stringify({
        hasRoot: Boolean(document.getElementById('root')),
        hasBanner: text.includes('PAPER MODE') || text.toLowerCase().includes('paper mode'),
        hasTitle: text.includes('AI TRADER'),
        hasOffline: text.includes('BACKEND OFFLINE'),
        hasLiveOrderFalse: text.includes('LIVE ORDER EXECUTION: FALSE'),
        text: text.slice(0, 1200),
      })
    })()
  \`)
  const image = await win.webContents.capturePage()
  require('node:fs').writeFileSync(join(${JSON.stringify(join(root, 'smoke'))}, 'screenshot.png'), image.toPNG())
  process.stdout.write(probe)
  app.quit()
})
app.on('window-all-closed', () => app.quit())
`,
  'utf8',
)

const child = spawn(electron, [harness], {
  cwd: root,
  env: { ...process.env, ELECTRON_DISABLE_SECURITY_WARNINGS: '1' },
  stdio: ['ignore', 'pipe', 'pipe'],
})

let out = ''
let err = ''
child.stdout.on('data', (chunk) => (out += chunk.toString()))
child.stderr.on('data', (chunk) => (err += chunk.toString()))

const timer = setTimeout(() => {
  child.kill()
  console.error('TIMEOUT waiting for electron smoke test')
  process.exit(1)
}, 90_000)

child.on('exit', (code) => {
  clearTimeout(timer)
  if (code !== 0) {
    console.error(`electron exited ${code}\n${err}`)
    process.exit(1)
  }
  try {
    const parsed = JSON.parse(out)
    console.log(JSON.stringify(parsed, null, 2))
    if (!parsed.hasRoot || !parsed.hasTitle || !parsed.hasLiveOrderFalse) {
      console.error('SMOKE FAILED: shell did not render as expected')
      process.exit(1)
    }
    console.log('\nSMOKE OK')
    process.exit(0)
  } catch {
    console.error(`unparseable smoke output:\n${out}\n${err}`)
    process.exit(1)
  }
})
