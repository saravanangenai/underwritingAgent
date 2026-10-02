import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import { api, ApiError, UNAUTHORIZED_EVENT } from '../api'
import App from '../App.vue'
import { hasRole, session } from '../auth'
import { makeRouter } from '../router'
import type { DecisionLogEntry, User } from '../types'

afterEach(() => vi.restoreAllMocks())
enableAutoUnmount(afterEach) // each test's app stops listening for window events

const person = (username: string, role: User['role']): User => ({
  username,
  display_name: username.toUpperCase(),
  role,
  active: true,
})
const ADA = person('ada', 'admin')
const UMA = person('uma', 'underwriter')
const VIC = person('vic', 'viewer')

async function mountAt(path: string, user: User | null) {
  session.user = null
  session.checked = false
  vi.spyOn(api, 'me').mockResolvedValue(user)
  vi.spyOn(api, 'verifyAudit').mockResolvedValue({
    ok: true,
    entries_checked: 0,
    first_bad_entry_id: null,
    reason: null,
  })
  vi.spyOn(api, 'listSubmissions').mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 })
  const router = makeRouter(createMemoryHistory())
  await router.push(path)
  const app = mount(App, { global: { plugins: [router] } })
  await flushPromises()
  return { app, router }
}

async function fillLogin(app: Awaited<ReturnType<typeof mountAt>>['app'], user: string, pass: string) {
  const [username, password] = app.findAll('input')
  await username.setValue(user)
  await password.setValue(pass)
  await app.find('form').trigger('submit')
  await flushPromises()
}

describe('roles', () => {
  it('ranks viewer < underwriter < admin', () => {
    expect(hasRole('viewer', VIC)).toBe(true)
    expect(hasRole('underwriter', VIC)).toBe(false)
    expect(hasRole('underwriter', ADA)).toBe(true)
    expect(hasRole('admin', UMA)).toBe(false)
    expect(hasRole('viewer', null)).toBe(false)
  })
})

describe('sign-in', () => {
  it('sends a signed-out visitor to sign in and remembers where they were going', async () => {
    const { app, router } = await mountAt('/submissions/SUB-1', null)
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query).toEqual({ next: '/submissions/SUB-1' })
    expect(app.find('nav').exists()).toBe(false)
    expect(api.verifyAudit).not.toHaveBeenCalled() // no audit banner before sign-in
  })

  it('signs in and continues to the page they asked for', async () => {
    const login = vi.spyOn(api, 'login').mockResolvedValue({ user: UMA, token: 't', expires_at: '' })
    vi.spyOn(api, 'getSubmission').mockRejectedValue(new ApiError(404, 'Submission not found'))
    vi.spyOn(api, 'getDecision').mockResolvedValue(null)
    vi.spyOn(api, 'getHistory').mockResolvedValue([])
    const { app, router } = await mountAt('/login?next=/submissions/SUB-1', null)
    await fillLogin(app, '  uma ', 'test-password-123')
    expect(login).toHaveBeenCalledWith('uma', 'test-password-123')
    expect(router.currentRoute.value.fullPath).toBe('/submissions/SUB-1')
    expect(app.text()).toContain('UMA')
  })

  it('never follows an off-site next link after sign-in', async () => {
    vi.spyOn(api, 'login').mockResolvedValue({ user: UMA, token: 't', expires_at: '' })
    const { app, router } = await mountAt('/login?next=//evil.example.com', null)
    await fillLogin(app, 'uma', 'test-password-123')
    expect(router.currentRoute.value.fullPath).toBe('/')
  })

  it('shows the API message for a wrong password', async () => {
    vi.spyOn(api, 'login').mockRejectedValue(new ApiError(401, 'Invalid username or password'))
    const { app } = await mountAt('/login', null)
    await fillLogin(app, 'uma', 'wrong-password')
    expect(app.find('[role="alert"]').text()).toBe('Invalid username or password')
  })

  it('returns to sign-in when the session expires mid-use', async () => {
    const { router } = await mountAt('/', UMA)
    window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT))
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('login')
    expect(session.user).toBeNull()
  })

  it('signs out', async () => {
    const logout = vi.spyOn(api, 'logout').mockResolvedValue(null)
    const { app, router } = await mountAt('/', UMA)
    const button = app.findAll('button').find((b) => b.text() === 'Sign out')
    await button!.trigger('click')
    await flushPromises()
    expect(logout).toHaveBeenCalled()
    expect(router.currentRoute.value.name).toBe('login')
  })
})

describe('what each role sees', () => {
  it.each([
    [VIC, ['Queue']],
    [UMA, ['Queue', 'New submission']],
    [ADA, ['Queue', 'New submission', 'Users']],
  ])('%o sees the right menu', async (user, links) => {
    const { app } = await mountAt('/', user)
    expect(app.findAll('nav a').map((a) => a.text())).toEqual(links)
  })

  it('keeps viewers out of New submission and non-admins out of Users', async () => {
    const first = await mountAt('/new', VIC)
    expect(first.router.currentRoute.value.fullPath).toBe('/')
    const second = await mountAt('/users', UMA)
    expect(second.router.currentRoute.value.fullPath).toBe('/')
  })
})

describe('Users page', () => {
  it('lists users and creates a new one', async () => {
    const list = vi.spyOn(api, 'listUsers').mockResolvedValue([ADA, UMA])
    const create = vi.spyOn(api, 'createUser').mockResolvedValue(person('neil', 'viewer'))
    const { app } = await mountAt('/users', ADA)
    expect(app.findAll('tbody tr')).toHaveLength(2)

    const form = app.findAll('form').at(-1)!
    const inputs = form.findAll('input')
    await inputs[0].setValue('Neil')
    await inputs[1].setValue('Neil New')
    await form.find('select').setValue('viewer')
    await inputs[2].setValue('another-long-pass')
    await form.trigger('submit')
    await flushPromises()
    expect(create).toHaveBeenCalledWith({
      username: 'neil',
      display_name: 'Neil New',
      role: 'viewer',
      password: 'another-long-pass',
    })
    expect(list).toHaveBeenCalledTimes(2)
    expect(app.text()).toContain('Created NEIL (neil)')
  })

  it('does not let an admin deactivate themselves', async () => {
    vi.spyOn(api, 'listUsers').mockResolvedValue([ADA, UMA])
    const { app } = await mountAt('/users', ADA)
    const rows = app.findAll('tbody tr')
    const deactivate = (i: number) => rows[i].findAll('button').find((b) => b.text() === 'Deactivate')!
    expect(deactivate(0).attributes('disabled')).toBeDefined()
    expect(deactivate(1).attributes('disabled')).toBeUndefined()
  })
})

describe('LLM recommendation label', () => {
  const declined: DecisionLogEntry = {
    submission_id: 'SUB-HS',
    decision: {
      action: 'auto_decline',
      confidence: 1,
      rationale: 'Hard-stop rule(s) triggered: HS-001',
      estimated_premium: null,
    },
    risk_memo: {
      flags: [
        { rule_id: 'HS-001', issue: 'Roof 26 years', severity: 'hard_stop', source_field: 'p.age', origin: 'rule' },
      ],
      narrative: 'Memo.',
      composite_risk_score: 40,
      assessed_by: 'groq/openai/gpt-oss-120b',
      recommendation: { action: 'refer', confidence: 0.85, rationale: 'Roof is old.' },
      rejected_flags: [],
    },
    model_version: 'risk=groq/openai/gpt-oss-120b',
    rules_version: 'r',
    created_at: '2026-09-29T10:00:00',
    inputs: {},
    prompt_versions: {},
    llm_review_raw: null,
    review: null,
    prev_hash: null,
    entry_hash: null,
  }
  const status = (value: 'declined' | 'referred') => ({
    submission_id: 'SUB-HS',
    status: value,
    created_at: '2026-09-29T10:00:00',
    updated_at: '2026-09-29T10:00:00',
  })

  it('says a hard stop overrode the LLM', async () => {
    vi.spyOn(api, 'getSubmission').mockResolvedValue(status('declined'))
    vi.spyOn(api, 'getDecision').mockResolvedValue(declined)
    vi.spyOn(api, 'getHistory').mockResolvedValue([declined])
    const { app } = await mountAt('/submissions/SUB-HS', UMA)
    expect(app.find('.recommendation').text()).toContain(
      'Not applied: hard-stop rule HS-001 declined this automatically.',
    )
  })

  it('says the LLM agrees when it also recommends decline', async () => {
    const agreeing: DecisionLogEntry = {
      ...declined,
      risk_memo: {
        ...declined.risk_memo,
        recommendation: { action: 'decline', confidence: 0.96, rationale: 'Roof is old.' },
      },
    }
    vi.spyOn(api, 'getSubmission').mockResolvedValue(status('declined'))
    vi.spyOn(api, 'getDecision').mockResolvedValue(agreeing)
    vi.spyOn(api, 'getHistory').mockResolvedValue([agreeing])
    const { app } = await mountAt('/submissions/SUB-HS', UMA)
    const text = app.find('.recommendation').text()
    expect(text).toContain('Agrees with the rules: hard-stop rule HS-001 declined this.')
    expect(text).not.toContain('Not applied')
  })

  it('adds nothing when the decision matches, and viewers get no review panel', async () => {
    const matching: DecisionLogEntry = {
      ...declined,
      decision: { ...declined.decision, action: 'refer_to_underwriter' },
      risk_memo: { ...declined.risk_memo, flags: [] },
    }
    vi.spyOn(api, 'getSubmission').mockResolvedValue(status('referred'))
    vi.spyOn(api, 'getDecision').mockResolvedValue(matching)
    vi.spyOn(api, 'getHistory').mockResolvedValue([matching])
    const { app } = await mountAt('/submissions/SUB-HS', VIC)
    expect(app.find('.recommendation__applied').exists()).toBe(false)
    expect(app.text()).not.toContain('Underwriter review')
  })
})
