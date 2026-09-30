<script setup lang="ts">
import { computed, ref } from 'vue'

import { api, ApiError } from '../api'
import { session } from '../auth'
import type { ReviewAction } from '../types'

const props = defineProps<{ submissionId: string }>()
const emit = defineEmits<{ reviewed: [] }>()

const NOTE_MIN = 10

const OPTIONS: { value: ReviewAction; label: string; hint: string }[] = [
  { value: 'approve', label: 'Approve', hint: 'Bind at the quoted terms.' },
  { value: 'request_info', label: 'Request information', hint: 'Send back to the applicant.' },
  { value: 'decline', label: 'Decline', hint: 'The risk is outside appetite.' },
]

const action = ref<ReviewAction | null>(null)
const note = ref('')
const submitting = ref(false)
const error = ref<string | null>(null)

const noteTooShort = computed(() => note.value.trim().length < NOTE_MIN)
const canSubmit = computed(() => action.value !== null && !noteTooShort.value)
const buttonLabel = computed(
  () => OPTIONS.find((o) => o.value === action.value)?.label ?? 'Record decision',
)

async function submit() {
  if (!canSubmit.value || action.value === null) return
  submitting.value = true
  error.value = null
  try {
    await api.review(props.submissionId, { action: action.value, note: note.value.trim() })
    emit('reviewed')
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'The review could not be recorded.'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <section class="card card--accent" aria-labelledby="review-heading">
    <header class="card__header">
      <h2 id="review-heading">Underwriter review</h2>
      <span class="muted small">This submission is waiting for your decision</span>
    </header>
    <form @submit.prevent="submit">
      <fieldset class="choices">
        <legend class="visually-hidden">Decision</legend>
        <label v-for="o in OPTIONS" :key="o.value" class="choice" :class="{ 'is-selected': action === o.value }">
          <input v-model="action" type="radio" name="review-action" :value="o.value" />
          <span>
            <strong>{{ o.label }}</strong>
            <span class="muted small">{{ o.hint }}</span>
          </span>
        </label>
      </fieldset>
      <label class="field field--full">Note for the audit record
        <textarea v-model="note" rows="3" placeholder="What did you check, and why this decision?" />
      </label>
      <p class="muted small" :class="{ 'text-warn': note.length > 0 && noteTooShort }">
        At least {{ NOTE_MIN }} characters. The note is stored in the tamper-evident audit log.
      </p>
      <p class="small">
        Recording as <strong>{{ session.user?.display_name }}</strong>&nbsp;<span class="muted">({{ session.user?.username }})</span>
      </p>
      <p v-if="error" class="notice notice--bad" role="alert">{{ error }}</p>
      <div class="actions">
        <button type="submit" class="button button--primary" :disabled="!canSubmit || submitting">
          {{ submitting ? 'Recording…' : buttonLabel }}
        </button>
      </div>
    </form>
  </section>
</template>
