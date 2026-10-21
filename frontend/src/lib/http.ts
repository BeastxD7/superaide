import axios, { AxiosError, type AxiosInstance } from 'axios'

// The backend is a separate API server (FastAPI on port 8000). The browser never
// calls it directly: it calls /api on this Next.js server, which forwards the
// request (see src/app/api/[...path]/route.ts). Cookies therefore belong to the
// app's own origin, whichever host name the page was opened with.
export const API_URL = '/api'

export class ApiError extends Error {
  status: number
  field?: string
  constructor(status: number, message: string, field?: string) {
    super(message)
    this.status = status
    this.field = field
  }
}

type Options = {
  // Operator console: a bearer token. The app itself uses the httpOnly session cookie instead.
  token?: () => string | null
  withCredentials?: boolean
}

export function createClient(baseURL: string, { token, withCredentials = false }: Options = {}): AxiosInstance {
  const client = axios.create({
    baseURL,
    timeout: 120_000,
    withCredentials,
    // A custom header makes the browser check CORS first, so other sites can't
    // send changes with our cookie (CSRF protection; the API requires it).
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'superaide' },
  })
  client.interceptors.request.use((config) => {
    const t = token?.()
    if (t) config.headers.Authorization = `Bearer ${t}`
    return config
  })
  client.interceptors.response.use(
    (res) => res,
    (err: AxiosError<{ detail?: unknown }>) => {
      if (!err.response) {
        return Promise.reject(new ApiError(0, "Can't reach Superaide. Check your connection, or that start.command is still running."))
      }
      const { status, data } = err.response
      const detail = data?.detail
      if (status === 404 && detail === 'Not Found') {
        return Promise.reject(
          new ApiError(404, 'The Superaide API is an older version without this feature. Stop it and run start.command again.'),
        )
      }
      if (detail && typeof detail === 'object' && 'message' in detail) {
        const d = detail as { message: string; field?: string }
        return Promise.reject(new ApiError(status, d.message, d.field))
      }
      if (Array.isArray(detail)) {
        // FastAPI validation errors: [{loc: [..., field], msg}]
        const first = detail[0] as { loc?: string[]; msg?: string } | undefined
        return Promise.reject(new ApiError(status, first?.msg ?? 'Check the form and try again.', first?.loc?.at(-1)))
      }
      return Promise.reject(new ApiError(status, typeof detail === 'string' ? detail : err.message))
    },
  )
  return client
}
