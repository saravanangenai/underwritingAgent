<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api, ApiError } from '../api'
import type { NeedsMoreInfoResponse } from '../types'

// Synthetic sample, same as the backend's samples/acord_text.json.
const SAMPLE_ACORD = `ACORD 80 HOMEOWNER APPLICATION (SYNTHETIC SAMPLE)
APPLICANT: Sample Applicant
LOCATION OF PROPERTY: 415 Harbor View Ln, San Clemente, CA 92672
PROPOSED EFFECTIVE DATE: 12/01/2026
COVERAGES: A - DWELLING $720,000    DEDUCTIBLE (ALL PERILS): $2,500
RATING INFORMATION: YEAR BUILT 2008   CONSTRUCTION: Masonry   SQ FT: 2,650
ROOF: Tile, replaced 2019
DISTANCE TO COAST: 3.5 miles
SWIMMING POOL: Y (in-ground, fenced)    TRAMPOLINE: N
PRIOR CARRIER: Example Mutual, 6 years
LOSS HISTORY (LAST 5 YEARS): None reported`

function inDays(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() + days)
  return d.toISOString().slice(0, 10)
}

const router = useRouter()
const mode = ref<'form' | 'acord'>('form')
const acordText = ref('')
const form = reactive({
  address_line1: '',
  city: '',
  state: 'CA',
  zip: '',
  year_built: '' as number | '',
  roof_year: '' as number | '',
  coastal_distance_miles: '' as number | '',
  has_pool: false,
  has_trampoline: false,
  coverage_a_limit: '' as number | '',
  requested_effective_date: inDays(30),
  carrier: '',
  claims_last_5_years: 0 as number | '',
})

const submitting = ref(false)
const error = ref<string | null>(null)
const missing = ref<NeedsMoreInfoResponse | null>(null)

/** Blank optional fields are left out, so the backend applies its defaults. */
function compact(obj: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== '' && v !== null))
}

function payload(): Record<string, unknown> {
  if (mode.value === 'acord') return { acord_text: acordText.value }
  return {
    property: compact({
      address_line1: form.address_line1.trim(),
      city: form.city.trim(),
      state: form.state.trim().toUpperCase(),
      zip: form.zip.trim(),
      year_built: form.year_built,
      roof_year: form.roof_year,
      coastal_distance_miles: form.coastal_distance_miles,
      has_pool: form.has_pool,
      has_trampoline: form.has_trampoline,
    }),
    coverage: compact({
      coverage_a_limit: form.coverage_a_limit,
      requested_effective_date: form.requested_effective_date,
    }),
    prior_insurance: compact({
      carrier: form.carrier.trim(),
      claims_last_5_years: form.claims_last_5_years,
    }),
  }
}

async function submit() {
  submitting.value = true
  error.value = null
  missing.value = null
  try {
    const outcome = await api.underwrite(payload())
    if (outcome.kind === 'decided') {
      await router.push(`/submissions/${encodeURIComponent(outcome.data.submission_id)}`)
    } else {
      missing.value = outcome.data
    }
  } catch (e) {
    error.value =
      e instanceof ApiError && e.status === 503
        ? `No LLM is available to read ACORD text: ${e.message}`
        : e instanceof ApiError
          ? e.message
          : 'The submission failed.'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="page-header">
    <div>
      <h1>New submission</h1>
      <p class="muted">Runs the full pipeline: intake, enrichment, risk review, triage and audit.</p>
    </div>
  </div>

  <div class="tabs" role="tablist" aria-label="Submission format">
    <button type="button" role="tab" class="tab" :class="{ 'is-active': mode === 'form' }"
      :aria-selected="mode === 'form'" @click="mode = 'form'">Structured form</button>
    <button type="button" role="tab" class="tab" :class="{ 'is-active': mode === 'acord' }"
      :aria-selected="mode === 'acord'" @click="mode = 'acord'">Raw ACORD text</button>
  </div>

  <form class="card" @submit.prevent="submit">
    <template v-if="mode === 'form'">
      <fieldset>
        <legend>Property</legend>
        <div class="fields">
          <label class="field field--wide">Street address
            <input v-model="form.address_line1" autocomplete="off" placeholder="22 Willow Creek Dr" />
          </label>
          <label class="field">City <input v-model="form.city" placeholder="Irvine" /></label>
          <label class="field field--narrow">State <input v-model="form.state" maxlength="2" /></label>
          <label class="field field--narrow">ZIP <input v-model="form.zip" inputmode="numeric" maxlength="5" /></label>
          <label class="field">Year built <input v-model.number="form.year_built" type="number" min="1800" /></label>
          <label class="field">Roof year <input v-model.number="form.roof_year" type="number" min="1800" /></label>
          <label class="field">Miles to coast
            <input v-model.number="form.coastal_distance_miles" type="number" min="0" step="0.1" />
          </label>
          <label class="check"><input v-model="form.has_pool" type="checkbox" /> Pool</label>
          <label class="check"><input v-model="form.has_trampoline" type="checkbox" /> Trampoline</label>
        </div>
      </fieldset>
      <fieldset>
        <legend>Coverage and history</legend>
        <div class="fields">
          <label class="field">Coverage A limit ($)
            <input v-model.number="form.coverage_a_limit" type="number" min="1" step="any" />
          </label>
          <label class="field">Effective date <input v-model="form.requested_effective_date" type="date" /></label>
          <label class="field">Prior carrier <input v-model="form.carrier" /></label>
          <label class="field">Claims in last 5 years
            <input v-model.number="form.claims_last_5_years" type="number" min="0" />
          </label>
        </div>
      </fieldset>
      <p class="muted small">Use synthetic applicants only.</p>
    </template>

    <template v-else>
      <label class="field field--full">Application text
        <textarea v-model="acordText" rows="14" spellcheck="false"
          placeholder="Paste ACORD 80 style application text…" />
      </label>
      <p class="muted small">
        An LLM extracts the fields, then the same checks run as for the form. Needs an LLM key on
        the backend.
        <button type="button" class="link" @click="acordText = SAMPLE_ACORD">Insert a sample</button>
      </p>
    </template>

    <div v-if="missing" class="notice notice--warn" role="alert">
      <strong>More information needed</strong> for {{ missing.submission_id }}:
      <ul>
        <li v-for="m in missing.missing_fields" :key="m">{{ m }}</li>
      </ul>
    </div>
    <p v-if="error" class="notice notice--bad" role="alert">{{ error }}</p>

    <div class="actions">
      <button type="submit" class="button button--primary"
        :disabled="submitting || (mode === 'acord' && !acordText.trim())">
        {{ submitting ? 'Underwriting…' : 'Underwrite' }}
      </button>
    </div>
  </form>
</template>
