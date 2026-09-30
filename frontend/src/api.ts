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
} from './types'

export const API_URL = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/\/$/, '')

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { 'content-type': 'application/json', ...init?.headers },
    })
  } catch {
    throw new ApiError(0, `Cannot reach the API at ${API_URL}. Is the backend running?`)
  }
  const body: unknown = await response.json().catch(() => null)
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

export const api = {
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
