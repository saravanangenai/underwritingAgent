<script setup lang="ts">
import { formatDateTime, shortHash } from '../format'
import type { DecisionLogEntry } from '../types'
import StatusBadge from './StatusBadge.vue'

defineProps<{ entries: DecisionLogEntry[] }>()
</script>

<template>
  <ol class="timeline">
    <li v-for="entry in entries" :key="entry.entry_hash ?? entry.created_at" class="timeline__item">
      <div class="timeline__head">
        <StatusBadge :value="entry.decision.action" />
        <span class="muted small">{{ formatDateTime(entry.created_at) }}</span>
      </div>
      <p v-if="entry.review" class="timeline__body">
        <strong>{{ entry.review.reviewer }}</strong>: {{ entry.review.note }}
      </p>
      <p v-else class="timeline__body">
        <span class="muted">Automated · {{ entry.risk_memo.assessed_by }}.</span>
        {{ entry.decision.rationale }}
      </p>
      <p class="mono small muted" :title="entry.entry_hash ?? ''">entry {{ shortHash(entry.entry_hash) }}</p>
    </li>
  </ol>
</template>
