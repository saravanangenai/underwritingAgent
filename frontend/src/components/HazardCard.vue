<script setup lang="ts">
import { computed } from 'vue'

import type { EnrichmentResult } from '../types'

const props = defineProps<{ enrichment: EnrichmentResult[] }>()

interface Peril {
  code?: string
  hazard?: string
  score?: number
  rating?: string
}

const hazard = computed(() =>
  props.enrichment.find((e) => e.source === 'hazard_apify' || e.source === 'hazard_mock'),
)
const data = computed(() => (hazard.value?.data ?? {}) as Record<string, unknown>)
const isLive = computed(() => hazard.value?.source === 'hazard_apify')
const unavailable = computed(() => data.value.status === 'unavailable')
const score = computed(() =>
  typeof data.value.composite_score === 'number' ? data.value.composite_score : null,
)
const perils = computed(() =>
  Array.isArray(data.value.top_hazards) ? (data.value.top_hazards as Peril[]) : [],
)
const location = computed(() =>
  [data.value.county && `${data.value.county} County`, data.value.state, data.value.tract_fips && `tract ${data.value.tract_fips}`]
    .filter(Boolean)
    .join(' · '),
)
</script>

<template>
  <section class="card">
    <header class="card__header">
      <h2>Hazard</h2>
      <span v-if="hazard" class="tag">{{ isLive ? 'FEMA NRI (live)' : 'Mock data' }}</span>
    </header>
    <p v-if="!hazard" class="muted">No hazard lookup was recorded.</p>
    <p v-else-if="unavailable" class="notice notice--warn">
      Hazard data unavailable: {{ data.error }}. Review hazard exposure manually (HZ-002).
    </p>
    <template v-else>
      <div v-if="score !== null" class="score">
        <div class="score__value">{{ score }}</div>
        <div class="score__detail">
          <div>{{ data.tier ?? 'Composite score' }}</div>
          <div class="meter" role="meter" :aria-valuenow="score" aria-valuemin="0" aria-valuemax="100"
            aria-label="Composite hazard score">
            <span class="meter__fill" :style="{ width: `${Math.min(100, score)}%` }" />
          </div>
          <div class="muted small">
            <template v-if="isLive">FEMA national percentile, 0-100 · </template>HZ-001 refers at 70
          </div>
        </div>
      </div>
      <ul v-if="perils.length" class="perils">
        <li v-for="peril in perils" :key="peril.code ?? peril.hazard">
          <span>{{ peril.hazard ?? peril.code }}</span>
          <span class="muted">{{ peril.rating }}<template v-if="peril.score !== undefined"> · {{ peril.score }}</template></span>
        </li>
      </ul>
      <p v-if="location" class="muted small">{{ location }}</p>
      <p v-if="data.geocoded_address" class="muted small">Geocoded to {{ data.geocoded_address }}</p>
      <p v-if="data.note" class="muted small">{{ data.note }}</p>
    </template>
  </section>
</template>
