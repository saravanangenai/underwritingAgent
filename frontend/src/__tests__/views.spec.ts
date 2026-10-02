import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import { api } from '../api'
import { session } from '../auth'
import App from '../App.vue'
import { makeRouter } from '../router'
import type { DecisionLogEntry, SubmissionPage, User } from '../types'

const UMA: User = { username: 'uma', display_name: 'Uma Underwriter', role: 'underwriter', active: true }

afterEach(() => vi.restoreAllMocks())
enableAutoUnmount(afterEach) // each test's app stops listening for window events

const PAGE: SubmissionPage = {
  total: 1,
  limit: 25,
  offset: 0,
  items: [
    {
      submission_id: 'SUB-ABC',
      status: 'referred',
      created_at: '2026-09-29T10:00:00',
      updated_at: '2026-09-29T10:00:01',
      property_address: '9 Canyon Ridge Rd, Ventura, CA 93001',
      action: 'refer_to_underwriter',
      confidence: 0.8,
      flags: ['SR-010', 'UW-105'],
      assessed_by: 'groq/openai/gpt-oss-120b',
      reviewed_by: null,
    },
  ],
}

const ENTRY: DecisionLogEntry = {
  submission_id: 'SUB-ABC',
  decision: {
    action: 'refer_to_underwriter',
    confidence: 0.8,
    rationale: 'Caution flag(s) need underwriter review: SR-010',
    estimated_premium: 3600,
  },
  risk_memo: {
    flags: [
      { rule_id: 'SR-010', issue: 'Pool present', severity: 'caution', source_field: 'submission.has_pool', origin: 'rule' },
    ],
    narrative: 'The pool needs a fence check.',
    composite_risk_score: 40,
    assessed_by: 'groq/openai/gpt-oss-120b',
    recommendation: { action: 'refer', confidence: 0.8, rationale: 'Pool liability.' },
    rejected_flags: ['UW-999: unknown rule id'],
  },
  model_version: 'risk=groq/openai/gpt-oss-120b',
  rules_version: '2026-09-phase5',
  created_at: '2026-09-29T10:00:01',
  inputs: { submission: { property_address: '9 Canyon Ridge Rd', has_pool: true }, enrichment: [] },
  prompt_versions: { risk_review: 'db779957cbc8' },
  llm_review_raw: null,
  review: null,
  prev_hash: '0'.repeat(64),
  entry_hash: 'abcdef0123456789'.repeat(4),
}

async function mountAt(path: string, user: User | null = UMA) {
  // Each test starts signed out; the router's guard asks the API who is signed in.
  session.user = null
  session.checked = false
  vi.spyOn(api, 'me').mockResolvedValue(user)
  vi.spyOn(api, 'verifyAudit').mockResolvedValue({
    ok: true,
    entries_checked: 1,
    first_bad_entry_id: null,
    reason: null,
  })
  // Detail pages also load the history; tests that care override this.
  if (!vi.isMockFunction(api.getHistory)) vi.spyOn(api, 'getHistory').mockResolvedValue([])
  const router = makeRouter(createMemoryHistory())
  await router.push(path)
  const app = mount(App, { global: { plugins: [router] } })
  await flushPromises()
  return { app, router }
}

describe('QueueView', () => {
  it('lists submissions with their flags and links to the detail page', async () => {
    vi.spyOn(api, 'listSubmissions').mockResolvedValue(PAGE)
    const { app } = await mountAt('/')
    const row = app.find('tbody tr')
    expect(row.text()).toContain('SUB-ABC')
    expect(row.text()).toContain('Referred')
    expect(row.text()).toContain('UW-105')
    expect(row.text()).toContain('80%')
    expect(row.find('a').attributes('href')).toBe('/submissions/SUB-ABC')
  })

  it('filters by status through the URL', async () => {
    const list = vi.spyOn(api, 'listSubmissions').mockResolvedValue(PAGE)
    const { app, router } = await mountAt('/')
    const referred = app.findAll('.chip').find((c) => c.text() === 'Referred')!
    await referred.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ status: 'referred' })
    expect(list).toHaveBeenLastCalledWith({ status: 'referred', limit: 25, offset: 0 })
  })

  it('shows an error when the API is down', async () => {
    vi.spyOn(api, 'listSubmissions').mockRejectedValue(new Error('boom'))
    const { app } = await mountAt('/')
    expect(app.find('[role="alert"]').text()).toContain('Could not load submissions')
  })
})

describe('SubmissionView', () => {
  it('shows the decision, flags, memo, rejected flags and audit record', async () => {
    vi.spyOn(api, 'getSubmission').mockResolvedValue({
      submission_id: 'SUB-ABC',
      status: 'referred',
      created_at: '2026-09-29T10:00:00',
      updated_at: '2026-09-29T10:00:01',
    })
    vi.spyOn(api, 'getDecision').mockResolvedValue(ENTRY)
    const { app } = await mountAt('/submissions/SUB-ABC')
    const text = app.text()
    expect(text).toContain('Refer to underwriter') // the decision badge
    expect(text).toContain('submission.has_pool')
    expect(text).toContain('The pool needs a fence check.')
    expect(text).toContain('LLM recommends refer')
    expect(text).toContain('1 LLM flag(s) rejected')
    expect(text).toContain('db779957cbc8')
    expect(text).toContain('2026-09-phase5')
  })

  it('shows the JSON the LLM received next to the memo, and hides it for rules-only memos', async () => {
    const status = {
      submission_id: 'SUB-ABC',
      status: 'referred' as const,
      created_at: '2026-09-29T10:00:00',
      updated_at: '2026-09-29T10:00:01',
    }
    const withData: DecisionLogEntry = {
      ...ENTRY,
      inputs: {
        submission: { has_pool: true, coastal_distance_miles: null },
        enrichment: [{ source: 'hazard_mock', data: { composite_score: 35 }, retrieved_at: 'x' }],
      },
    }
    vi.spyOn(api, 'getSubmission').mockResolvedValue(status)
    vi.spyOn(api, 'getDecision').mockResolvedValue(withData)
    const { app } = await mountAt('/submissions/SUB-ABC')
    const json = JSON.parse(app.find('pre.json').text())
    expect(json.today).toBe('2026-09-29')
    expect(json.submission.coastal_distance_miles).toBeNull()
    expect(json.enrichment.hazard_mock.composite_score).toBe(35)
    expect(json.rule_flags_already_raised.map((f: { rule_id: string }) => f.rule_id)).toEqual(['SR-010'])

    const rulesOnly: DecisionLogEntry = {
      ...withData,
      risk_memo: { ...withData.risk_memo, assessed_by: 'rules', recommendation: null },
    }
    vi.spyOn(api, 'getDecision').mockResolvedValue(rulesOnly)
    const again = await mountAt('/submissions/SUB-ABC')
    expect(again.app.find('pre.json').exists()).toBe(false)
  })

  it('explains a submission with no decision', async () => {
    vi.spyOn(api, 'getSubmission').mockResolvedValue({
      submission_id: 'SUB-INC',
      status: 'needs_more_info',
      created_at: '2026-09-29T10:00:00',
      updated_at: '2026-09-29T10:00:00',
    })
    vi.spyOn(api, 'getDecision').mockResolvedValue(null)
    const { app } = await mountAt('/submissions/SUB-INC')
    expect(app.text()).toContain('missing required fields')
  })
})

describe('NewSubmissionView', () => {
  it('sends the form as the nested payload and opens the decision', async () => {
    const underwrite = vi.spyOn(api, 'underwrite').mockResolvedValue({
      kind: 'decided',
      data: { submission_id: 'SUB-NEW' } as never,
    })
    vi.spyOn(api, 'getSubmission').mockResolvedValue({
      submission_id: 'SUB-NEW',
      status: 'completed',
      created_at: '2026-09-29T10:00:00',
      updated_at: '2026-09-29T10:00:00',
    })
    vi.spyOn(api, 'getDecision').mockResolvedValue(null)
    const { app, router } = await mountAt('/new')

    const inputs = app.findAll('input')
    await inputs[0].setValue('1 Test Ln')
    await inputs[1].setValue('Irvine')
    await inputs[3].setValue('92618')
    await app.find('form').trigger('submit')
    await flushPromises()

    const payload = underwrite.mock.calls[0][0] as Record<string, Record<string, unknown>>
    expect(payload.property).toMatchObject({
      address_line1: '1 Test Ln',
      city: 'Irvine',
      state: 'CA',
      zip: '92618',
      has_pool: false,
    })
    expect(payload.property).not.toHaveProperty('year_built') // blanks are left out
    expect(payload.coverage.requested_effective_date).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(router.currentRoute.value.fullPath).toBe('/submissions/SUB-NEW')
  })

  it('lists missing fields when more information is needed', async () => {
    vi.spyOn(api, 'underwrite').mockResolvedValue({
      kind: 'needs_more_info',
      data: {
        submission_id: 'SUB-X',
        status: 'needs_more_info',
        missing_fields: ['coverage.coverage_a_limit is required'],
      },
    })
    const { app } = await mountAt('/new')
    await app.find('form').trigger('submit')
    await flushPromises()
    expect(app.find('[role="alert"]').text()).toContain('coverage.coverage_a_limit is required')
  })

  it('sends raw ACORD text in the text tab', async () => {
    const underwrite = vi.spyOn(api, 'underwrite').mockResolvedValue({
      kind: 'decided',
      data: { submission_id: 'SUB-T' } as never,
    })
    vi.spyOn(api, 'getSubmission').mockResolvedValue({
      submission_id: 'SUB-T',
      status: 'referred',
      created_at: '2026-09-29T10:00:00',
      updated_at: '2026-09-29T10:00:00',
    })
    vi.spyOn(api, 'getDecision').mockResolvedValue(null)
    const { app } = await mountAt('/new')
    await app.findAll('[role="tab"]')[1].trigger('click')
    await app.find('.link').trigger('click') // insert the sample
    await app.find('form').trigger('submit')
    await flushPromises()
    expect((underwrite.mock.calls[0][0] as { acord_text: string }).acord_text).toContain('ACORD 80')
  })
})

describe('NewSubmissionView validation', () => {
  // The browser silently blocks the submit when any field fails constraint validation, and
  // unit tests that trigger 'submit' directly skip that check. So test the fields themselves.
  it.each([
    ['Coverage A limit', ['600000', '725500', '1200000']],
    ['Year built', ['1925', '2010']],
    ['Roof year', ['2001', '2024']],
    ['Miles to coast', ['0.3', '3.5', '12']],
    ['Claims in last 5 years', ['0', '2']],
  ])('%s accepts realistic values', async (label, values) => {
    const { app } = await mountAt('/new')
    const field = app.findAll('label').find((l) => l.text().startsWith(label))!
    const input = field.find('input').element as HTMLInputElement
    for (const value of values) {
      input.value = value
      expect(input.checkValidity(), `${label} = ${value}`).toBe(true)
    }
  })

  it('rejects fractional claim counts', async () => {
    const { app } = await mountAt('/new')
    const field = app.findAll('label').find((l) => l.text().startsWith('Claims'))!
    const input = field.find('input').element as HTMLInputElement
    input.value = '1.5'
    expect(input.checkValidity()).toBe(false)
  })
})

describe('Underwriter review', () => {
  const REFERRED = {
    submission_id: 'SUB-ABC',
    status: 'referred' as const,
    created_at: '2026-09-29T10:00:00',
    updated_at: '2026-09-29T10:00:01',
  }
  const REVIEWED: DecisionLogEntry = {
    ...ENTRY,
    decision: { ...ENTRY.decision, action: 'underwriter_approve', confidence: null },
    review: {
      action: 'approve',
      note: 'Fence and gate confirmed by photos.',
      reviewer: 'Uma Underwriter (uma)',
      reviewed_at: '2026-09-29T11:00:00',
      supersedes_entry_hash: ENTRY.entry_hash,
    },
    entry_hash: 'feedfacecafebeef'.repeat(4),
  }

  it('is offered only while a submission is referred', async () => {
    vi.spyOn(api, 'getSubmission').mockResolvedValue({ ...REFERRED, status: 'completed' })
    vi.spyOn(api, 'getDecision').mockResolvedValue(ENTRY)
    const { app } = await mountAt('/submissions/SUB-ABC')
    expect(app.text()).not.toContain('Underwriter review')
  })

  it('needs a decision, a real note and a name before it can be sent', async () => {
    vi.spyOn(api, 'getSubmission').mockResolvedValue(REFERRED)
    vi.spyOn(api, 'getDecision').mockResolvedValue(ENTRY)
    const { app } = await mountAt('/submissions/SUB-ABC')
    const panel = app.find('[aria-labelledby="review-heading"]')
    const button = () => panel.find('button[type="submit"]')

    expect(button().attributes('disabled')).toBeDefined()
    await panel.find('input[value="approve"]').setValue(true)
    await panel.find('textarea').setValue('too short')
    expect(button().attributes('disabled')).toBeDefined()
    await panel.find('textarea').setValue('Fence and gate confirmed by photos.')
    expect(button().attributes('disabled')).toBeUndefined()
    expect(button().text()).toBe('Approve')
  })

  it('records the decision, then shows it and the history', async () => {
    const getSubmission = vi.spyOn(api, 'getSubmission').mockResolvedValue(REFERRED)
    const getDecision = vi.spyOn(api, 'getDecision').mockResolvedValue(ENTRY)
    const getHistory = vi.spyOn(api, 'getHistory').mockResolvedValue([ENTRY])
    const review = vi.spyOn(api, 'review').mockImplementation(async () => {
      getSubmission.mockResolvedValue({ ...REFERRED, status: 'completed' })
      getDecision.mockResolvedValue(REVIEWED)
      getHistory.mockResolvedValue([ENTRY, REVIEWED])
      return REVIEWED
    })
    const { app, router } = await mountAt('/submissions/SUB-ABC')
    const panel = app.find('[aria-labelledby="review-heading"]')
    await panel.find('input[value="approve"]').setValue(true)
    expect(panel.text()).toContain('Recording as Uma Underwriter')
    await panel.find('textarea').setValue('  Fence and gate confirmed by photos.  ')
    await panel.find('form').trigger('submit')
    await flushPromises()

    expect(review).toHaveBeenCalledWith('SUB-ABC', {
      action: 'approve',
      note: 'Fence and gate confirmed by photos.',
    })
    expect(router.currentRoute.value.query).toEqual({ reviewed: '1' })
    const text = app.text()
    expect(text).toContain('Decision recorded by Uma Underwriter (uma)')
    expect(text).not.toContain('Underwriter review') // resolved, so no panel
    expect(app.find('.timeline').text()).toContain('Fence and gate confirmed by photos.')
  })

  it('shows the API error when the review is refused', async () => {
    const { ApiError } = await import('../api')
    vi.spyOn(api, 'getSubmission').mockResolvedValue(REFERRED)
    vi.spyOn(api, 'getDecision').mockResolvedValue(ENTRY)
    vi.spyOn(api, 'review').mockRejectedValue(
      new ApiError(409, 'Only referred submissions can be reviewed; this one is completed'),
    )
    const { app } = await mountAt('/submissions/SUB-ABC')
    const panel = app.find('[aria-labelledby="review-heading"]')
    await panel.find('input[value="decline"]').setValue(true)
    await panel.find('textarea').setValue('Outside appetite for this territory.')
    await panel.find('form').trigger('submit')
    await flushPromises()
    expect(panel.find('[role="alert"]').text()).toContain('this one is completed')
  })
})
