import { reactive } from 'vue'

import { api } from './api'
import type { Role, User } from './types'

const RANK: Record<Role, number> = { viewer: 0, underwriter: 1, admin: 2 }

/** Who is signed in. `checked` turns true after the first /auth/me answer. */
export const session = reactive<{ user: User | null; checked: boolean }>({
  user: null,
  checked: false,
})

export async function loadSession(): Promise<User | null> {
  try {
    session.user = await api.me()
  } catch {
    session.user = null // API unreachable: treat as signed out; pages show the error
  }
  session.checked = true
  return session.user
}

export async function signIn(username: string, password: string): Promise<User> {
  const { user } = await api.login(username, password)
  session.user = user
  session.checked = true
  return user
}

export async function signOut(): Promise<void> {
  try {
    await api.logout()
  } finally {
    session.user = null
  }
}

/** True when the signed-in user's role is `minimum` or higher. */
export function hasRole(minimum: Role, user: User | null = session.user): boolean {
  return user !== null && RANK[user.role] >= RANK[minimum]
}
