import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The frontend runs on its own server (port 3000) and calls the backend API on
// port 8000 with Axios (see src/http.ts). Set VITE_API_URL to point elsewhere.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { host: '127.0.0.1', port: 3000, strictPort: true },
  preview: { host: '127.0.0.1', port: 3000, strictPort: true },
})
