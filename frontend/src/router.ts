import { createRouter, createWebHistory, type RouterHistory } from 'vue-router'

import NewSubmissionView from './views/NewSubmissionView.vue'
import QueueView from './views/QueueView.vue'
import SubmissionView from './views/SubmissionView.vue'

export const routes = [
  { path: '/', name: 'queue', component: QueueView, meta: { title: 'Queue' } },
  { path: '/new', name: 'new', component: NewSubmissionView, meta: { title: 'New submission' } },
  {
    path: '/submissions/:id',
    name: 'submission',
    component: SubmissionView,
    props: true,
    meta: { title: 'Submission' },
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export function makeRouter(history: RouterHistory = createWebHistory()) {
  const router = createRouter({ history, routes })
  router.afterEach((to) => {
    document.title = `${String(to.meta.title ?? 'Workbench')} · Underwriting Workbench`
  })
  return router
}
