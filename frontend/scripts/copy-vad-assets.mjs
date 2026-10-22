// Copies the voice activity detector's files (Silero VAD v5, its audio worklet, and the ONNX
// runtime's WebAssembly) from node_modules into public/vad/, so the browser loads them from this
// site and never from a CDN (ADR 0012). Runs before `next dev` and `next build`.
import { copyFileSync, existsSync, mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const out = join(root, 'public', 'vad')
const files = [
  ['@ricky0123/vad-web/dist', 'vad.worklet.bundle.min.js'],
  ['@ricky0123/vad-web/dist', 'silero_vad_v5.onnx'],
  ['onnxruntime-web/dist', 'ort-wasm-simd-threaded.wasm'],
  ['onnxruntime-web/dist', 'ort-wasm-simd-threaded.mjs'],
]
mkdirSync(out, { recursive: true })
for (const [dir, name] of files) {
  const from = join(root, 'node_modules', dir, name)
  if (!existsSync(from)) {
    console.warn(`copy-vad-assets: ${from} is missing; the voice call will fall back to typing`)
    continue
  }
  copyFileSync(from, join(out, name))
}
