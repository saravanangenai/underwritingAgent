import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError } from '../api'

function mockFetch(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(body === undefined ? null : JSON.stringify(body), { status }),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => vi.unstubAllGlobals())

describe('api', () => {
  it('builds the list query from filters and paging', async () => {
    const fetchMock = mockFetch(200, { items: [], total: 0, limit: 25, offset: 50 })
    await api.listSubmissions({ status: 'referred', limit: 25, offset: 50 })
    expect(fetchMock.mock.calls[0][0]).toBe(
      'http://localhost:8000/submissions?status=referred&limit=25&offset=50',
    )
  })

  it('treats a 422 with missing fields as an outcome, not an error', async () => {
    mockFetch(422, {
      submission_id: 'SUB-1',
      status: 'needs_more_info',
      missing_fields: ['coverage.coverage_a_limit is required'],
    })
    const outcome = await api.underwrite({})
    expect(outcome.kind).toBe('needs_more_info')
  })

  it('returns the decision for a successful underwrite', async () => {
    const fetchMock = mockFetch(200, { submission_id: 'SUB-2', status: 'completed' })
    const outcome = await api.underwrite({ property: {} })
    expect(outcome).toMatchObject({ kind: 'decided', data: { submission_id: 'SUB-2' } })
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'POST' })
  })

  it('raises the API detail for other errors', async () => {
    mockFetch(503, { detail: 'All LLM providers failed' })
    await expect(api.underwrite({ acord_text: 'x' })).rejects.toMatchObject({
      status: 503,
      message: 'All LLM providers failed',
    })
  })

  it('joins FastAPI validation errors into one message', async () => {
    mockFetch(422, { detail: [{ loc: ['query', 'limit'], msg: 'must be at least 1' }] })
    await expect(api.listSubmissions({ limit: 0 })).rejects.toThrow('query.limit: must be at least 1')
  })

  it('returns null when a submission has no decision', async () => {
    mockFetch(404, { detail: 'No decision recorded for this submission' })
    expect(await api.getDecision('SUB-3')).toBeNull()
  })

  it('explains an unreachable backend', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    const error = await api.verifyAudit().catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).message).toContain('Is the backend running?')
  })
})
