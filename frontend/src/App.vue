<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import { UNAUTHORIZED_EVENT } from './api'
import { hasRole, session, signOut } from './auth'
import AuditBanner from './components/AuditBanner.vue'

const route = useRoute()
const router = useRouter()

// A request came back 401: the session expired or was revoked. Ask the user to sign in again.
function onUnauthorized() {
  if (!session.user) return
  session.user = null
  router.push({ name: 'login', query: { next: route.fullPath } })
}

async function logOut() {
  await signOut()
  await router.push({ name: 'login' })
}

onMounted(() => window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized))
onBeforeUnmount(() => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized))
</script>

<template>
  <header class="app-header">
    <div class="app-header__inner">
      <RouterLink to="/" class="brand">
        <span class="brand__mark" aria-hidden="true">UW</span>
        <span>Underwriting Workbench</span>
      </RouterLink>
      <nav v-if="session.user" class="nav" aria-label="Main">
        <RouterLink to="/" class="nav__link" exact-active-class="is-active">Queue</RouterLink>
        <RouterLink v-if="hasRole('underwriter')" to="/new" class="nav__link" active-class="is-active">
          New submission
        </RouterLink>
        <RouterLink v-if="hasRole('admin')" to="/users" class="nav__link" active-class="is-active">
          Users
        </RouterLink>
      </nav>
      <div v-if="session.user" class="user-menu">
        <span class="small">
          {{ session.user.display_name }}
          <span class="tag">{{ session.user.role }}</span>
        </span>
        <button type="button" class="button" @click="logOut">Sign out</button>
      </div>
    </div>
  </header>
  <AuditBanner v-if="session.user" />
  <main class="page">
    <RouterView />
  </main>
</template>
