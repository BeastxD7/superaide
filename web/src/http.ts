import axios, { AxiosError, type AxiosInstance } from 'axios'

// The backend is a separate API server (FastAPI on port 8000). The frontend
// (this Vite app on port 3000) reaches it over HTTP with Axios.
// Override with VITE_API_URL in web/.env.local if the API lives elsewhere.
export const API_URL: string = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') || 'http://127.0.0.1:8000'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export function createClient(baseURL: string, token: () => string | null): AxiosInstance {
  const client = axios.create({ baseURL, timeout: 120_000, headers: { 'Content-Type': 'application/json' } })
  client.interceptors.request.use((config) => {
    const t = token()
    if (t) config.headers.Authorization = `Bearer ${t}`
    return config
  })
  client.interceptors.response.use(
    (res) => res,
    (err: AxiosError<{ detail?: unknown }>) => {
      if (!err.response) {
        return Promise.reject(
          new ApiError(0, `Can't reach the agent's API at ${baseURL}. Is start.command still running?`),
        )
      }
      const { status, data } = err.response
      if (status === 404 && typeof data?.detail === 'string' && data.detail === 'Not Found') {
        return Promise.reject(
          new ApiError(404, `The API at ${baseURL} is an older version without this feature. Stop it and run start.command again.`),
        )
      }
      const detail = typeof data?.detail === 'string' ? data.detail : err.message
      return Promise.reject(new ApiError(status, detail))
    },
  )
  return client
}
