
const { app, BrowserWindow } = require('electron')
const { join } = require('node:path')
app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1600, height: 1000, show: false,
    webPreferences: {
      preload: join("D:\\trading\\ai-trader-ui\\out", 'preload/index.mjs'),
      contextIsolation: true, nodeIntegration: false, sandbox: true,
    },
  })
  await win.loadFile(join("D:\\trading\\ai-trader-ui\\out", 'renderer/index.html'))
  await new Promise((r) => setTimeout(r, 4000))
  const probe = await win.webContents.executeJavaScript(`
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
  `)
  const image = await win.webContents.capturePage()
  require('node:fs').writeFileSync(join("D:\\trading\\ai-trader-ui\\smoke", 'screenshot.png'), image.toPNG())
  process.stdout.write(probe)
  app.quit()
})
app.on('window-all-closed', () => app.quit())
