<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api, ApiError } from '../api'
import type { AuditVerification } from '../types'

const result = ref<AuditVerification | null>(null)
const error = ref<string | null>(null)
const route = useRoute()

async function check() {
  try {
    result.value = await api.verifyAudit()
    error.value = null
  } catch (e) {
    result.value = null
    error.value = e instanceof ApiError ? e.message : 'Could not verify the audit log.'
  }
}

onMounted(check)
// Re-check after navigation, e.g. once a new decision has been logged.
watch(() => route.fullPath, check)
</script>

<template>
  <div v-if="error" class="banner banner--neutral" role="status">
    <span aria-hidden="true">○</span> Audit status unavailable: {{ error }}
  </div>
  <div v-else-if="result && !result.ok" class="banner banner--bad" role="alert">
    <strong>Audit chain broken</strong> at entry #{{ result.first_bad_entry_id }}:
    {{ result.reason }}. Decisions from that entry on may have been altered.
  </div>
  <div v-else-if="result" class="banner banner--good" role="status">
    <span aria-hidden="true">✓</span> Audit chain verified · {{ result.entries_checked }}
    {{ result.entries_checked === 1 ? 'decision' : 'decisions' }}
  </div>
</template>
