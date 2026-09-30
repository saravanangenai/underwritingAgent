import type { Action, Severity, SubmissionStatusValue } from './types'

export const STATUS_LABELS: Record<SubmissionStatusValue, string> = {
  running: 'Running',
  completed: 'Approved',
  referred: 'Referred',
  declined: 'Declined',
  needs_more_info: 'Needs info',
  failed: 'Failed',
}

export const ACTION_LABELS: Record<Action, string> = {
  auto_approve: 'Auto-approve',
  auto_decline: 'Auto-decline',
  refer_to_underwriter: 'Refer to underwriter',
}

export const SEVERITY_LABELS: Record<Severity, string> = {
  hard_stop: 'Hard stop',
  caution: 'Caution',
  info: 'Info',
}

/** Tone used for badges: maps statuses and actions onto a few semantic colours. */
export type Tone = 'good' | 'warn' | 'bad' | 'neutral'

export function statusTone(status: SubmissionStatusValue | Action | null | undefined): Tone {
  switch (status) {
    case 'completed':
    case 'auto_approve':
      return 'good'
    case 'referred':
    case 'refer_to_underwriter':
    case 'needs_more_info':
      return 'warn'
    case 'declined':
    case 'auto_decline':
    case 'failed':
      return 'bad'
    default:
      return 'neutral'
  }
}

const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

/** The API sends naive UTC timestamps; mark them as UTC before formatting. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '-'
  const date = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`)
  return Number.isNaN(date.getTime()) ? value : dateTime.format(date)
}

export function formatMoney(value: number | null | undefined): string {
  if (value === null || value === undefined) return '-'
  return value.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
}

export function formatConfidence(value: number | null | undefined): string {
  return value === null || value === undefined ? '-' : `${Math.round(value * 100)}%`
}

export function shortHash(value: string | null | undefined): string {
  return value ? `${value.slice(0, 10)}…` : '-'
}
