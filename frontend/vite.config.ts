import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 打包产物放在 app-assets/，避免与后端的 /assets/stations（工位素材）冲突
export default defineConfig({
  plugins: [react()],
  base: '/',
  build: { assetsDir: 'app-assets', chunkSizeWarningLimit: 1500 },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
      '/assets/stations': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
})
