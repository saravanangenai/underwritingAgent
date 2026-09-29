<script setup lang="ts">
import { computed } from 'vue'

import { SEVERITY_LABELS } from '../format'
import type { RiskFlag } from '../types'

const props = defineProps<{ flags: RiskFlag[] }>()

const ORDER = { hard_stop: 0, caution: 1, info: 2 } as const
const sorted = computed(() =>
  [...props.flags].sort((a, b) => ORDER[a.severity] - ORDER[b.severity]),
)
</script>

<template>
  <p v-if="!flags.length" class="muted">No flags were raised.</p>
  <div v-else class="table-wrap">
    <table class="table">
      <thead>
        <tr>
          <th scope="col">Rule</th>
          <th scope="col">Severity</th>
          <th scope="col">Raised by</th>
          <th scope="col">Issue</th>
          <th scope="col">Cited field</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="flag in sorted" :key="flag.rule_id">
          <td class="mono">{{ flag.rule_id }}</td>
          <td>
            <span class="severity" :class="`severity--${flag.severity}`">
              {{ SEVERITY_LABELS[flag.severity] }}
            </span>
          </td>
          <td>
            <span class="origin" :class="`origin--${flag.origin}`">
              {{ flag.origin === 'llm' ? 'LLM' : 'Rule' }}
            </span>
          </td>
          <td>{{ flag.issue }}</td>
          <td class="mono">{{ flag.source_field }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
