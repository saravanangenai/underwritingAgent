<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { api, ApiError, type UserChanges } from '../api'
import { session } from '../auth'
import type { Role, User } from '../types'

const ROLES: { value: Role; label: string }[] = [
  { value: 'viewer', label: 'Viewer: read only' },
  { value: 'underwriter', label: 'Underwriter: submit and review' },
  { value: 'admin', label: 'Admin: also manage users' },
]

const users = ref<User[]>([])
const error = ref<string | null>(null)
const notice = ref<string | null>(null)
const form = reactive({ username: '', display_name: '', role: 'underwriter' as Role, password: '' })
const creating = ref(false)

function message(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.message : fallback
}

async function load() {
  try {
    users.value = await api.listUsers()
    error.value = null
  } catch (e) {
    error.value = message(e, 'Could not load users.')
  }
}

async function create() {
  creating.value = true
  error.value = null
  notice.value = null
  try {
    const user = await api.createUser({ ...form, username: form.username.trim().toLowerCase() })
    notice.value = `Created ${user.display_name} (${user.username}). Share the password with them securely.`
    Object.assign(form, { username: '', display_name: '', role: 'underwriter', password: '' })
    await load()
  } catch (e) {
    error.value = message(e, 'Could not create the user.')
  } finally {
    creating.value = false
  }
}

async function update(user: User, changes: UserChanges, done: string) {
  error.value = null
  notice.value = null
  try {
    await api.updateUser(user.username, changes)
    notice.value = done
    await load()
  } catch (e) {
    error.value = message(e, 'Could not update the user.')
  }
}

function resetPassword(user: User) {
  const password = window.prompt(`New password for ${user.display_name} (at least 12 characters):`)
  if (password) {
    update(user, { password }, `Password reset for ${user.username}; they were signed out.`)
  }
}

onMounted(load)
</script>

<template>
  <div class="page-header">
    <div>
      <h1>Users</h1>
      <p class="muted">Changing a role, deactivating or resetting a password signs that user out.</p>
    </div>
  </div>

  <p v-if="error" class="notice notice--bad" role="alert">{{ error }}</p>
  <p v-if="notice" class="notice notice--good" role="status">{{ notice }}</p>

  <section class="card card--flush">
    <div class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th scope="col">User</th>
            <th scope="col">Role</th>
            <th scope="col">Status</th>
            <th scope="col"><span class="visually-hidden">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="user in users" :key="user.username">
            <td>
              {{ user.display_name }}
              <div class="mono muted">{{ user.username }}</div>
            </td>
            <td>
              <select
                :value="user.role"
                :aria-label="`Role for ${user.username}`"
                @change="update(user, { role: ($event.target as HTMLSelectElement).value as Role }, `Role changed for ${user.username}.`)"
              >
                <option v-for="r in ROLES" :key="r.value" :value="r.value">{{ r.value }}</option>
              </select>
            </td>
            <td>
              <span class="badge" :class="user.active ? 'badge--good' : 'badge--neutral'">
                {{ user.active ? 'Active' : 'Inactive' }}
              </span>
            </td>
            <td class="actions">
              <button type="button" class="button" @click="resetPassword(user)">Reset password</button>
              <button
                type="button"
                class="button"
                :disabled="user.username === session.user?.username"
                :title="user.username === session.user?.username ? 'You cannot deactivate yourself' : ''"
                @click="update(user, { active: !user.active }, `${user.username} ${user.active ? 'deactivated' : 'reactivated'}.`)"
              >
                {{ user.active ? 'Deactivate' : 'Reactivate' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>

  <form class="card" @submit.prevent="create">
    <h2>Add a user</h2>
    <div class="fields">
      <label class="field">Username
        <input v-model="form.username" autocomplete="off" pattern="[a-zA-Z0-9][a-zA-Z0-9._\-]{1,63}" required />
      </label>
      <label class="field">Display name <input v-model="form.display_name" required minlength="2" /></label>
      <label class="field">Role
        <select v-model="form.role">
          <option v-for="r in ROLES" :key="r.value" :value="r.value">{{ r.label }}</option>
        </select>
      </label>
      <label class="field">Initial password
        <input v-model="form.password" type="password" autocomplete="new-password" minlength="12" required />
      </label>
    </div>
    <div class="actions">
      <button type="submit" class="button button--primary" :disabled="creating">
        {{ creating ? 'Creating…' : 'Create user' }}
      </button>
    </div>
  </form>
</template>
