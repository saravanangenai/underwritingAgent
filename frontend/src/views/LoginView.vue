<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '../api'
import { signIn } from '../auth'

const route = useRoute()
const router = useRouter()

const username = ref('')
const password = ref('')
const submitting = ref(false)
const error = ref<string | null>(null)

/** Only follow same-app paths after sign-in, never an outside URL. */
function nextPath(): string {
  const next = route.query.next
  return typeof next === 'string' && next.startsWith('/') && !next.startsWith('//') ? next : '/'
}

async function submit() {
  submitting.value = true
  error.value = null
  try {
    await signIn(username.value.trim(), password.value)
    password.value = ''
    await router.replace(nextPath())
  } catch (e) {
    error.value =
      e instanceof ApiError && (e.status === 401 || e.status === 429)
        ? e.message
        : e instanceof ApiError
          ? `Sign-in failed: ${e.message}`
          : 'Sign-in failed.'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="login">
    <form class="card login__card" @submit.prevent="submit">
      <h1>Sign in</h1>
      <p class="muted">Underwriting Workbench</p>
      <label class="field">Username
        <input v-model="username" autocomplete="username" autocapitalize="none" required autofocus />
      </label>
      <label class="field">Password
        <input v-model="password" type="password" autocomplete="current-password" required />
      </label>
      <p v-if="error" class="notice notice--bad" role="alert">{{ error }}</p>
      <button type="submit" class="button button--primary" :disabled="submitting || !username || !password">
        {{ submitting ? 'Signing in…' : 'Sign in' }}
      </button>
      <p class="muted small">
        Accounts are created by an admin. The first admin is created on the server with
        <code>python -m underwriting_agent.auth create</code>.
      </p>
    </form>
  </div>
</template>
