import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import { api } from '../api'
import AuditBanner from '../components/AuditBanner.vue'
import FlagTable from '../components/FlagTable.vue'
import HazardCard from '../components/HazardCard.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { makeRouter } from '../router'
import type { RiskFlag } from '../types'

afterEach(() => vi.restoreAllMocks())

describe('StatusBadge', () => {
  it.each([
    ['completed', 'Approved', 'badge--good'],
    ['referred', 'Referred', 'badge--warn'],
    ['declined', 'Declined', 'badge--bad'],
    ['refer_to_underwriter', 'Refer to underwriter', 'badge--warn'],
  ] as const)('shows %s as %s', (value, label, tone) => {
    const badge = mount(StatusBadge, { props: { value } })
    expect(badge.text()).toBe(label)
    expect(badge.classes()).toContain(tone)
  })
})

describe('FlagTable', () => {
  const flags: RiskFlag[] = [
    { rule_id: 'INFO-001', issue: 'Roof noted', severity: 'info', source_field: 'p.roof', origin: 'rule' },
    {
      rule_id: 'UW-105',
      issue: 'Roof older than house',
      severity: 'caution',
      source_field: 'submission.roof_year',
      origin: 'llm',
    },
    { rule_id: 'HS-001', issue: 'Old roof', severity: 'hard_stop', source_field: 'p.age', origin: 'rule' },
  ]

  it('orders hard stops first and marks who raised each flag', () => {
    const rows = mount(FlagTable, { props: { flags } }).findAll('tbody tr')
    expect(rows.map((r) => r.find('td').text())).toEqual(['HS-001', 'UW-105', 'INFO-001'])
    expect(rows[1].text()).toContain('LLM')
    expect(rows[1].text()).toContain('submission.roof_year')
  })

  it('says when there are no flags', () => {
    expect(mount(FlagTable, { props: { flags: [] } }).text()).toContain('No flags')
  })
})

describe('HazardCard', () => {
  it('shows live FEMA data with the top perils', () => {
    const card = mount(HazardCard, {
      props: {
        enrichment: [
          {
            source: 'hazard_apify',
            retrieved_at: '2026-09-29T00:00:00',
            data: {
              status: 'ok',
              composite_score: 89.96,
              tier: 'Relatively High',
              top_hazards: [{ code: 'IFLD', hazard: 'Inland Flooding', score: 95.8, rating: 'Very High' }],
              county: 'Orange',
              state: 'CA',
            },
          },
        ],
      },
    })
    expect(card.text()).toContain('FEMA NRI (live)')
    expect(card.text()).toContain('89.96')
    expect(card.text()).toContain('Inland Flooding')
    expect(card.text()).toContain('Orange County')
    expect(card.text()).toContain('FEMA national percentile')
  })

  it('explains an unavailable lookup', () => {
    const card = mount(HazardCard, {
      props: {
        enrichment: [
          {
            source: 'hazard_apify',
            retrieved_at: '2026-09-29T00:00:00',
            data: { status: 'unavailable', error: 'geocoder found no match' },
          },
        ],
      },
    })
    expect(card.text()).toContain('Hazard data unavailable')
    expect(card.text()).toContain('HZ-002')
  })
})

describe('AuditBanner', () => {
  async function mountBanner() {
    const router = makeRouter(createMemoryHistory())
    await router.push('/')
    const banner = mount(AuditBanner, { global: { plugins: [router] } })
    await flushPromises()
    return banner
  }

  it('confirms an intact chain', async () => {
    vi.spyOn(api, 'verifyAudit').mockResolvedValue({
      ok: true,
      entries_checked: 3,
      first_bad_entry_id: null,
      reason: null,
    })
    expect((await mountBanner()).text()).toContain('Audit chain verified · 3 decisions')
  })

  it('alerts when the chain is broken', async () => {
    vi.spyOn(api, 'verifyAudit').mockResolvedValue({
      ok: false,
      entries_checked: 3,
      first_bad_entry_id: 2,
      reason: 'content does not match entry_hash (the row was edited)',
    })
    const banner = await mountBanner()
    expect(banner.find('[role="alert"]').text()).toContain('Audit chain broken at entry #2')
  })
})
