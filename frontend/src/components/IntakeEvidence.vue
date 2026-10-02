<script setup lang="ts">
import { computed } from 'vue'

import { formatConfidence } from '../format'
import type { IntakeEvidenceEntry } from '../types'

const props = defineProps<{ evidence: Record<string, IntakeEvidenceEntry> }>()

const STATUS = {
  verified: { label: 'Verified', tone: 'good' },
  no_evidence: { label: 'No quote', tone: 'warn' },
  low_confidence: { label: 'Dropped: low confidence', tone: 'bad' },
  quote_not_found: { label: 'Dropped: quote not in text', tone: 'bad' },
} as const

// Problems first, so an underwriter sees what to double-check without scrolling.
const ORDER = { quote_not_found: 0, low_confidence: 1, no_evidence: 2, verified: 3 } as const
const rows = computed(() =>
  Object.entries(props.evidence).sort(([, a], [, b]) => ORDER[a.status] - ORDER[b.status]),
)
</script>

<template>
  <div class="table-wrap">
    <table class="table">
      <thead>
        <tr>
          <th scope="col">Field</th>
          <th scope="col">Value read</th>
          <th scope="col">Confidence</th>
          <th scope="col">Status</th>
          <th scope="col">Quoted from the application</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="[path, e] in rows" :key="path">
          <td class="mono">{{ path }}</td>
          <td class="mono">{{ e.value }}</td>
          <td>{{ e.confidence == null ? '-' : formatConfidence(e.confidence) }}</td>
          <td>
            <span class="badge" :class="`badge--${STATUS[e.status].tone}`">
              {{ STATUS[e.status].label }}
            </span>
          </td>
          <td>{{ e.quote ?? '-' }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
