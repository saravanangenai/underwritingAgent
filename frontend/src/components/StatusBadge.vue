<script setup lang="ts">
import { computed } from 'vue'

import { ACTION_LABELS, STATUS_LABELS, statusTone } from '../format'
import type { Action, SubmissionStatusValue } from '../types'

const props = defineProps<{ value: SubmissionStatusValue | Action | null | undefined }>()

const label = computed(() => {
  if (!props.value) return '-'
  return (
    STATUS_LABELS[props.value as SubmissionStatusValue] ??
    ACTION_LABELS[props.value as Action] ??
    props.value
  )
})
</script>

<template>
  <span class="badge" :class="`badge--${statusTone(value)}`">{{ label }}</span>
</template>
