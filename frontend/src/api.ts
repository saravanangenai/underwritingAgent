import type {
  AuditVerification,
  DecisionLogEntry,
  NeedsMoreInfoResponse,
  ReviewRequest,
  SubmissionPage,
  SubmissionStatus,
  SubmissionStatusValue,
  UnderwriteOutcome,
  UnderwriteResponse,
  LoginResponse,
  Role,
  User,
} from './types'

export const API_URL = (import.meta.env.VITE_API_URL ?? '/api').replace(/\/$/, '')

/** Fired when a signed-in request gets 401, so the app can send the user to sign in. */
export const UNAUTHORIZED_EVENT = 'uw:unauthorized'

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly body?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/** FastAPI puts a string in `detail` for our errors and a list for validation errors. */
function describe(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).join('.')}: ${d.msg}`)
      .join('; ')
  }
  return fallback
}

// Session checks and sign-in answer 401 as a normal outcome, not as an expired session.
const NO_REDIRECT = new Set(['/auth/me', '/auth/login'])

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      credentials: 'same-origin', // send the httpOnly session cookie
      headers: { 'content-type': 'application/json', ...init?.headers },
    })
  } catch {
    throw new ApiError(0, `Cannot reach the API at ${API_URL}. Is the backend running?`)
  }
  const body: unknown = response.status === 204 ? null : await response.json().catch(() => null)
  if (response.status === 401 && !NO_REDIRECT.has(path)) {
    window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT))
  }
  if (!response.ok) {
    throw new ApiError(response.status, describe(body, response.statusText), body)
  }
  return body as T
}

export interface ListParams {
  status?: SubmissionStatusValue
  limit?: number
  offset?: number
}

export interface NewUser {
  username: string
  display_name: string
  role: Role
  password: string
}

export interface UserChanges {
  display_name?: string
  role?: Role
  active?: boolean
  password?: string
}

export const api = {
  login(username: string, password: string): Promise<LoginResponse> {
    return request<LoginResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
  },

  logout(): Promise<null> {
    return request<null>('/auth/logout', { method: 'POST' })
  },

  /** The signed-in user, or null when not signed in. */
  async me(): Promise<User | null> {
    try {
      return await request<User>('/auth/me')
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return null
      throw error
    }
  },

  listUsers(): Promise<User[]> {
    return request<User[]>('/users')
  },

  createUser(user: NewUser): Promise<User> {
    return request<User>('/users', { method: 'POST', body: JSON.stringify(user) })
  },

  updateUser(username: string, changes: UserChanges): Promise<User> {
    return request<User>(`/users/${encodeURIComponent(username)}`, {
      method: 'PATCH',
      body: JSON.stringify(changes),
    })
  },

  listSubmissions(params: ListParams = {}): Promise<SubmissionPage> {
    const query = new URLSearchParams()
    if (params.status) query.set('status', params.status)
    if (params.limit !== undefined) query.set('limit', String(params.limit))
    if (params.offset !== undefined) query.set('offset', String(params.offset))
    const qs = query.toString()
    return request<SubmissionPage>(`/submissions${qs ? `?${qs}` : ''}`)
  },

  getSubmission(id: string): Promise<SubmissionStatus> {
    return request<SubmissionStatus>(`/submissions/${encodeURIComponent(id)}`)
  },

  /** The audit record, or null when the submission has no decision (e.g. needs more info). */
  async getDecision(id: string): Promise<DecisionLogEntry | null> {
    try {
      return await request<DecisionLogEntry>(`/submissions/${encodeURIComponent(id)}/decision`)
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return null
      throw error
    }
  },

  /** Every audit entry for the submission, oldest first. */
  getHistory(id: string): Promise<DecisionLogEntry[]> {
    return request<DecisionLogEntry[]>(`/submissions/${encodeURIComponent(id)}/history`)
  },

  /** Record an underwriter's decision on a referred submission. */
  review(id: string, body: ReviewRequest): Promise<DecisionLogEntry> {
    return request<DecisionLogEntry>(`/submissions/${encodeURIComponent(id)}/review`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
  },

  verifyAudit(): Promise<AuditVerification> {
    return request<AuditVerification>('/audit/verify')
  },

  /** Run the pipeline. A 422 carrying missing fields is an outcome, not an error. */
  async underwrite(payload: Record<string, unknown>): Promise<UnderwriteOutcome> {
    try {
      const data = await request<UnderwriteResponse>('/underwrite', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      return { kind: 'decided', data }
    } catch (error) {
      const body = error instanceof ApiError ? (error.body as NeedsMoreInfoResponse | null) : null
      if (error instanceof ApiError && error.status === 422 && body?.status === 'needs_more_info') {
        return { kind: 'needs_more_info', data: body }
      }
      throw error
    }
  },
}
