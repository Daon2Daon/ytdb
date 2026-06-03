import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// base는 프로덕션 빌드에서만 '/static/ui/' (FastAPI가 /static/ui/로 정적 서빙).
// dev 서버에서는 '/'로 두어 React Router basename '/app'과 충돌하지 않게 한다
// → dev 접속 URL: http://localhost:5173/app/
export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: command === 'build' ? '/static/ui/' : '/',
  build: {
    outDir: path.resolve(__dirname, '../app/static/ui'),
    emptyOutDir: true,
  },
  server: {
    proxy: { '/api': 'http://localhost:8000' },
  },
}))
