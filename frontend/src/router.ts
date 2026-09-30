import { createRouter, createWebHistory, type RouterHistory } from 'vue-router'

import { hasRole, loadSession, session } from './auth'
import type { Role } from './types'
import LoginView from './views/LoginView.vue'
import NewSubmissionView from './views/NewSubmissionView.vue'
import QueueView from './views/QueueView.vue'
import SubmissionView from './views/SubmissionView.vue'
import UsersView from './views/UsersView.vue'

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    public?: boolean // reachable without signing in
    role?: Role // minimum role
  }
}

export const routes = [
  { path: '/login', name: 'login', component: LoginView, meta: { title: 'Sign in', public: true } },
  { path: '/', name: 'queue', component: QueueView, meta: { title: 'Queue' } },
  {
    path: '/new',
    name: 'new',
    component: NewSubmissionView,
    meta: { title: 'New submission', role: 'underwriter' as Role },
  },
  {
    path: '/submissions/:id',
    name: 'submission',
    component: SubmissionView,
    props: true,
    meta: { title: 'Submission' },
  },
  { path: '/users', name: 'users', component: UsersView, meta: { title: 'Users', role: 'admin' as Role } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export function makeRouter(history: RouterHistory = createWebHistory()) {
  const router = createRouter({ history, routes })

  router.beforeEach(async (to) => {
    if (!session.checked) await loadSession()
    if (to.meta.public) {
      // Already signed in: skip the sign-in page.
      return to.name === 'login' && session.user ? { path: '/' } : true
    }
    if (!session.user) {
      return { name: 'login', query: to.fullPath !== '/' ? { next: to.fullPath } : {} }
    }
    if (to.meta.role && !hasRole(to.meta.role)) return { path: '/' }
    return true
  })

  router.afterEach((to) => {
    document.title = `${to.meta.title ?? 'Workbench'} · Underwriting Workbench`
  })
  return router
}
