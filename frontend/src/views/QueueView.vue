<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, ApiError } from '../api'
import StatusBadge from '../components/StatusBadge.vue'
import { formatConfidence, formatDateTime, STATUS_LABELS } from '../format'
import type { SubmissionPage, SubmissionStatusValue } from '../types'

const PAGE_SIZE = 25
const FILTERS: { value: SubmissionStatusValue | undefined; label: string }[] = [
  { value: 'referred', label: 'Referred' },
  { value: undefined, label: 'All' },
  { value: 'completed', label: STATUS_LABELS.completed },
  { value: 'declined', label: STATUS_LABELS.declined },
  { value: 'needs_more_info', label: STATUS_LABELS.needs_more_info },
  { value: 'failed', label: STATUS_LABELS.failed },
]

const route = useRoute()
const router = useRouter()

// Filter and page live in the URL, so a queue view can be bookmarked or shared.
const status = computed(() => route.query.status as SubmissionStatusValue | undefined)
const page = computed(() => Math.max(1, Number(route.query.page) || 1))

const data = ref<SubmissionPage | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    data.value = await api.listSubmissions({
      status: status.value,
      limit: PAGE_SIZE,
      offset: (page.value - 1) * PAGE_SIZE,
    })
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'Could not load submissions.'
  } finally {
    loading.value = false
  }
}

function setFilter(value: SubmissionStatusValue | undefined) {
  router.push({ query: value ? { status: value } : {} })
}

function goToPage(n: number) {
  router.push({ query: { ...route.query, page: n > 1 ? String(n) : undefined } })
}

const pages = computed(() => (data.value ? Math.max(1, Math.ceil(data.value.total / PAGE_SIZE)) : 1))

onMounted(load)
watch(() => route.query, load)
</script>

<template>
  <div class="page-header">
    <div>
      <h1>Submission queue</h1>
      <p class="muted">Newest first. Referred submissions need an underwriter's review.</p>
    </div>
    <div class="actions">
      <button type="button" class="button" :disabled="loading" @click="load">Refresh</button>
      <RouterLink to="/new" class="button button--primary">New submission</RouterLink>
    </div>
  </div>

  <div class="filters" role="group" aria-label="Filter by status">
    <button
      v-for="f in FILTERS"
      :key="f.label"
      type="button"
      class="chip"
      :class="{ 'is-active': status === f.value }"
      :aria-pressed="status === f.value"
      @click="setFilter(f.value)"
    >
      {{ f.label }}
    </button>
  </div>

  <p v-if="error" class="notice notice--bad" role="alert">{{ error }}</p>

  <section v-else class="card card--flush">
    <p v-if="loading && !data" class="empty">Loading…</p>
    <p v-else-if="data && !data.items.length" class="empty">
      No {{ status ? STATUS_LABELS[status].toLowerCase() : '' }} submissions yet.
      <RouterLink to="/new">Create one</RouterLink>.
    </p>
    <div v-else-if="data" class="table-wrap">
      <table class="table table--rows">
        <thead>
          <tr>
            <th scope="col">Submitted</th>
            <th scope="col">Submission</th>
            <th scope="col">Property</th>
            <th scope="col">Status</th>
            <th scope="col">Flags</th>
            <th scope="col" class="num">Confidence</th>
            <th scope="col">Assessed by</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in data.items" :key="item.submission_id">
            <td class="nowrap">{{ formatDateTime(item.created_at) }}</td>
            <td>
              <RouterLink :to="`/submissions/${encodeURIComponent(item.submission_id)}`" class="mono">
                {{ item.submission_id }}
              </RouterLink>
            </td>
            <td>{{ item.property_address ?? '-' }}</td>
            <td><StatusBadge :value="item.status" /></td>
            <td>
              <span v-for="flag in item.flags" :key="flag" class="tag mono">{{ flag }}</span>
              <span v-if="!item.flags.length" class="muted">-</span>
            </td>
            <td class="num">{{ formatConfidence(item.confidence) }}</td>
            <td class="small">
              {{ item.assessed_by ?? '-' }}
              <div v-if="item.reviewed_by" class="muted">reviewed by {{ item.reviewed_by }}</div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>

  <nav v-if="data && pages > 1" class="pager" aria-label="Pages">
    <button type="button" class="button" :disabled="page <= 1" @click="goToPage(page - 1)">
      Previous
    </button>
    <span class="muted">Page {{ page }} of {{ pages }} · {{ data.total }} submissions</span>
    <button type="button" class="button" :disabled="page >= pages" @click="goToPage(page + 1)">
      Next
    </button>
  </nav>
</template>
