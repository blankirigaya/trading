import { resolve } from 'node:path'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    build: {
      outDir: 'out/main',
      rollupOptions: {
        input: { index: resolve(__dirname, 'electron/main.ts') },
      },
    },
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      outDir: 'out/preload',
      rollupOptions: {
        input: { index: resolve(__dirname, 'electron/preload.ts') },
        // A sandboxed preload is executed as CommonJS by Electron, so the ESM output
        // this project emits by default ("type": "module") cannot be loaded there.
        output: { format: 'cjs', entryFileNames: '[name].js' },
      },
    },
  },
  renderer: {
    root: __dirname,
    base: './',
    resolve: {
      alias: { '@': resolve(__dirname, 'src') },
    },
    plugins: [react()],
    build: {
      outDir: 'out/renderer',
      emptyOutDir: true,
      sourcemap: true,
      rollupOptions: {
        input: { index: resolve(__dirname, 'index.html') },
      },
    },
  },
})
