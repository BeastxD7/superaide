import axios, { AxiosError, type AxiosInstance } from 'axios'

// The backend is a separate API server (FastAPI on port 8000). The browser never
// calls it directly: it calls /api on this Next.js server, which forwards the
// request (see src/app/api/[...path]/route.ts). Cookies therefore belong to the
// app's own origin, whichever host name the page was opened with.
export const API_URL = '/api'

export class ApiError extends Error {
  status: number
  field?: string
  // The stable machine code from the error envelope (ADR 0013), e.g. `invalid_cursor`. Absent on old bodies.
  code?: string
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
    (err: AxiosError<ErrorBody>) => {
      if (!err.response) {
        return Promise.reject(new ApiError(0, "Can't reach Superaide. Check your connection, or that start.command is still running."))
      }
      const { status, data } = err.response
      if (status === 404 && data?.detail === 'Not Found') {
        return Promise.reject(
          new ApiError(404, 'The Superaide API is an older version without this feature. Stop it and run start.command again.'),
        )
      }
      return Promise.reject(errorFromBody(status, data, err.message))
    },
  )
  return client
}

// Turn an API error body's `detail` into an ApiError (shared with fetch-based calls such as streaming chat).
export function errorFromDetail(status: number, detail: unknown, fallback: string): ApiError {
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const d = detail as { message: string; field?: string }
    return new ApiError(status, d.message, d.field)
  }
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc: [..., field], msg}]
    const first = detail[0] as { loc?: string[]; msg?: string } | undefined
    return new ApiError(status, first?.msg ?? 'Check the form and try again.', first?.loc?.at(-1))
  }
  return new ApiError(status, typeof detail === 'string' ? detail : fallback)
}

// The standard error body (ADR 0013): { status, status_code, message, data, error: { code, detail, request_id } }.
// `detail` at the top level is the old shape, kept for one release.
export type ErrorBody = {
  message?: string
  detail?: unknown
  error?: { code?: string; detail?: unknown; request_id?: string } | null
}

// Read an error response body: `error.message` first (the envelope's `message`), the old `detail` as the fallback.
export function errorFromBody(status: number, body: ErrorBody | null | undefined, fallback: string): ApiError {
  const err = readErrorBody(status, body, fallback)
  if (typeof body?.error?.code === 'string') err.code = body.error.code
  return err
}

function readErrorBody(status: number, body: ErrorBody | null | undefined, fallback: string): ApiError {
  const nested = body?.error?.detail
  // Validation: the per-field list keeps the field name so the form can mark it.
  if (Array.isArray(nested) || Array.isArray(body?.detail)) return errorFromDetail(status, nested ?? body?.detail, fallback)
  const field = nested && typeof nested === 'object' && 'field' in nested ? String((nested as { field: unknown }).field) : undefined
  if (typeof body?.message === 'string' && body.message) return new ApiError(status, body.message, field)
  return errorFromDetail(status, body?.detail, fallback)
}
