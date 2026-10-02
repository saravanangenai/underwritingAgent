// Mirrors the FastAPI schemas in src/underwriting_agent/schemas. Keep the two in step.

export type Role = 'viewer' | 'underwriter' | 'admin'

export interface User {
  username: string
  display_name: string
  role: Role
  active: boolean
}

export interface LoginResponse {
  user: User
  token: string
  expires_at: string
}

export type Action =
  | 'auto_approve'
  | 'auto_decline'
  | 'refer_to_underwriter'
  | 'underwriter_approve'
  | 'underwriter_decline'
  | 'underwriter_request_info'
export type ReviewAction = 'approve' | 'decline' | 'request_info'
export type Severity = 'info' | 'caution' | 'hard_stop'
export type SubmissionStatusValue =
  | 'running'
  | 'completed'
  | 'referred'
  | 'declined'
  | 'needs_more_info'
  | 'failed'

export interface RiskFlag {
  rule_id: string
  issue: string
  severity: Severity
  source_field: string
  origin: 'rule' | 'llm'
}

export interface Recommendation {
  action: 'approve' | 'refer' | 'decline'
  confidence: number
  rationale: string
}

export interface RiskMemo {
  flags: RiskFlag[]
  narrative: string
  composite_risk_score: number | null
  assessed_by: string
  recommendation: Recommendation | null
  rejected_flags: string[]
}

export interface TriageDecision {
  action: Action
  confidence: number | null
  rationale: string
  estimated_premium: number | null
}

export interface EnrichmentResult {
  source: string
  data: Record<string, unknown>
  retrieved_at: string
}

export interface UnderwriterReview {
  action: ReviewAction
  note: string
  reviewer: string
  reviewed_at: string
  supersedes_entry_hash: string | null
}

export interface ReviewRequest {
  action: ReviewAction
  note: string
}

export type IntakeEvidenceStatus = 'verified' | 'no_evidence' | 'low_confidence' | 'quote_not_found'

/** How one field was read from ACORD text: what the model quoted and how sure it was. */
export interface IntakeEvidenceEntry {
  value: unknown
  quote: string | null
  confidence: number | null
  status: IntakeEvidenceStatus
}

export interface DecisionLogEntry {
  submission_id: string
  decision: TriageDecision
  risk_memo: RiskMemo
  model_version: string
  rules_version: string
  created_at: string
  inputs: {
    submission?: Record<string, unknown>
    enrichment?: EnrichmentResult[]
    intake?: { evidence: Record<string, IntakeEvidenceEntry> }
  }
  prompt_versions: Record<string, string>
  llm_review_raw: Record<string, unknown> | null
  review: UnderwriterReview | null
  prev_hash: string | null
  entry_hash: string | null
}

export interface SubmissionStatus {
  submission_id: string
  status: SubmissionStatusValue
  created_at: string
  updated_at: string
  submitted_by?: string | null
}

export interface SubmissionSummary extends SubmissionStatus {
  property_address: string | null
  action: Action | null
  confidence: number | null
  flags: string[]
  assessed_by: string | null
  reviewed_by: string | null
}

export interface SubmissionPage {
  items: SubmissionSummary[]
  total: number
  limit: number
  offset: number
}

export interface AuditVerification {
  ok: boolean
  entries_checked: number
  first_bad_entry_id: number | null
  reason: string | null
}

export interface UnderwriteResponse {
  submission_id: string
  status: SubmissionStatusValue
  decision: TriageDecision
  risk_memo: RiskMemo
  enrichment: EnrichmentResult[]
}

export interface NeedsMoreInfoResponse {
  submission_id: string
  status: 'needs_more_info'
  missing_fields: string[]
}

export type UnderwriteOutcome =
  | { kind: 'decided'; data: UnderwriteResponse }
  | { kind: 'needs_more_info'; data: NeedsMoreInfoResponse }
