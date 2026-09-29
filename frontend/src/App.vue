<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'

import AuditBanner from './components/AuditBanner.vue'
</script>

<template>
  <header class="app-header">
    <div class="app-header__inner">
      <RouterLink to="/" class="brand">
        <span class="brand__mark" aria-hidden="true">UW</span>
        <span>Underwriting Workbench</span>
      </RouterLink>
      <nav class="nav" aria-label="Main">
        <RouterLink to="/" class="nav__link" exact-active-class="is-active">Queue</RouterLink>
        <RouterLink to="/new" class="nav__link" active-class="is-active">New submission</RouterLink>
      </nav>
    </div>
  </header>
  <AuditBanner />
  <main class="page">
    <RouterView />
  </main>
</template>
