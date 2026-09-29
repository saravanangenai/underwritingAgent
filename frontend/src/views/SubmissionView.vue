<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { api, ApiError } from '../api'
import FlagTable from '../components/FlagTable.vue'
import HazardCard from '../components/HazardCard.vue'
import StatusBadge from '../components/StatusBadge.vue'
import {
  formatConfidence,
  formatDateTime,
  formatMoney,
  shortHash,
} from '../format'
import type { DecisionLogEntry, SubmissionStatus } from '../types'

const props = defineProps<{ id: string }>()

const status = ref<SubmissionStatus | null>(null)
const entry = ref<DecisionLogEntry | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    const [s, d] = await Promise.all([api.getSubmission(props.id), api.getDecision(props.id)])
    status.value = s
    entry.value = d
  } catch (e) {
    error.value =
      e instanceof ApiError && e.status === 404
        ? `Submission ${props.id} was not found.`
        : e instanceof ApiError
          ? e.message
          : 'Could not load the submission.'
  } finally {
    loading.value = false
  }
}

watch(() => props.id, load, { immediate: true })

const submission = computed(() => entry.value?.inputs.submission ?? {})
const memo = computed(() => entry.value?.risk_memo)
const decision = computed(() => entry.value?.decision)
const llmReviewed = computed(() => memo.value && memo.value.assessed_by !== 'rules')

const facts = computed(() => {
  const s = submission.value as Record<string, unknown>
  const yes = (v: unknown) => (v ? 'Yes' : 'No')
  return [
    ['Year built', s.year_built ?? '-'],
    ['Roof year', s.roof_year ?? '-'],
    ['Coverage A', formatMoney(s.coverage_a_limit as number | undefined)],
    ['Effective date', s.requested_effective_date ?? '-'],
    ['Pool', yes(s.has_pool)],
    ['Trampoline', yes(s.has_trampoline)],
    ['Claims (5 yrs)', s.claims_last_5_years ?? '-'],
    ['Coast distance', s.coastal_distance_miles != null ? `${s.coastal_distance_miles} mi` : '-'],
    ['Prior carrier', s.prior_carrier ?? '-'],
  ] as const
})
</script>

<template>
  <RouterLink to="/" class="back">← Queue</RouterLink>

  <p v-if="loading" class="empty">Loading…</p>
  <p v-else-if="error" class="notice notice--bad" role="alert">{{ error }}</p>

  <template v-else-if="status">
    <div class="page-header">
      <div>
        <h1 class="mono">{{ status.submission_id }}</h1>
        <p class="muted">
          {{ (submission as Record<string, unknown>).property_address ?? 'No property address' }}
          · submitted {{ formatDateTime(status.created_at) }}
        </p>
      </div>
      <StatusBadge :value="status.status" />
    </div>

    <p v-if="!entry" class="notice notice--warn">
      <template v-if="status.status === 'needs_more_info'">
        This submission is missing required fields, so nothing was enriched or decided. Resubmit
        it with the missing information.
      </template>
      <template v-else-if="status.status === 'failed'">
        The pipeline failed for this submission (for example, no LLM was available to read ACORD
        text). No decision was recorded.
      </template>
      <template v-else>No decision has been recorded yet.</template>
    </p>

    <template v-else-if="decision && memo">
      <div class="grid">
        <section class="card">
          <header class="card__header">
            <h2>Decision</h2>
            <StatusBadge :value="decision.action" />
          </header>
          <p>{{ decision.rationale }}</p>
          <dl class="facts facts--inline">
            <div><dt>Confidence</dt><dd>{{ formatConfidence(decision.confidence) }}</dd></div>
            <div>
              <dt>Estimated premium</dt>
              <dd>{{ formatMoney(decision.estimated_premium) }} <span class="tag">mock</span></dd>
            </div>
            <div><dt>Assessed by</dt><dd class="small">{{ memo.assessed_by }}</dd></div>
          </dl>
        </section>

        <section class="card">
          <header class="card__header"><h2>Property</h2></header>
          <dl class="facts">
            <div v-for="[label, value] in facts" :key="label">
              <dt>{{ label }}</dt>
              <dd>{{ value }}</dd>
            </div>
          </dl>
        </section>
      </div>

      <section class="card">
        <header class="card__header">
          <h2>Risk flags</h2>
          <span class="muted small">Every flag cites the field that triggered it</span>
        </header>
        <FlagTable :flags="memo.flags" />
        <details v-if="memo.rejected_flags.length" class="rejected">
          <summary>{{ memo.rejected_flags.length }} LLM flag(s) rejected by validation</summary>
          <ul>
            <li v-for="r in memo.rejected_flags" :key="r" class="mono small">{{ r }}</li>
          </ul>
        </details>
      </section>

      <div class="grid">
        <section class="card">
          <header class="card__header">
            <h2>Risk memo</h2>
            <span class="tag">{{ llmReviewed ? 'LLM review' : 'Rules only' }}</span>
          </header>
          <p class="prose">{{ memo.narrative }}</p>
          <div v-if="memo.recommendation" class="recommendation">
            <strong>LLM recommends {{ memo.recommendation.action }}</strong>
            ({{ formatConfidence(memo.recommendation.confidence) }}):
            {{ memo.recommendation.rationale }}
            <p class="muted small">
              Guardrails: the LLM can make a decision more cautious, never less. Only rules decline.
            </p>
          </div>
        </section>

        <HazardCard :enrichment="entry.inputs.enrichment ?? []" />
      </div>

      <section class="card">
        <header class="card__header"><h2>Audit record</h2></header>
        <dl class="facts facts--inline">
          <div><dt>Logged</dt><dd>{{ formatDateTime(entry.created_at) }}</dd></div>
          <div><dt>Model version</dt><dd class="mono small">{{ entry.model_version }}</dd></div>
          <div><dt>Rules version</dt><dd class="mono small">{{ entry.rules_version }}</dd></div>
          <div v-for="(v, k) in entry.prompt_versions" :key="k">
            <dt>Prompt {{ String(k).replace('_', ' ') }}</dt>
            <dd class="mono small">{{ v }}</dd>
          </div>
          <div><dt>Entry hash</dt><dd class="mono small" :title="entry.entry_hash ?? ''">{{ shortHash(entry.entry_hash) }}</dd></div>
          <div><dt>Previous hash</dt><dd class="mono small" :title="entry.prev_hash ?? ''">{{ shortHash(entry.prev_hash) }}</dd></div>
        </dl>
      </section>
    </template>
  </template>
</template>
