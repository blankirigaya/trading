import { resolve } from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // Relative asset URLs so the built renderer also loads from file:// when
  // Electron loads the packaged index.html.
  base: './',
  resolve: {
    alias: { '@': resolve(__dirname, 'src') },
  },
  server: {
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: 'out/renderer',
    emptyOutDir: true,
    sourcemap: true,
  },
})
