// Response shapes of the REST API (backend/app/api); every list is a Page.
export type Page<T> = { items: T[]; total: number; skip: number; limit: number; nextLink: string | null };

export type Risk = "low" | "medium" | "high";
export type Status =
  | "pending_votes"
  | "pending_discussion"
  | "pending_approval"
  | "approved"
  | "rejected"
  | "expired"
  | "cancelled";

export type Decision = {
  id: string;
  title: string;
  description: string;
  action_type: string;
  payload: Record<string, unknown>;
  risk_level: Risk;
  effective_risk_level: Risk;
  status: Status;
  version: number;
  content_hash: string;
  requested_by_agent_id: string | null;
  requested_by_user_id: string | null;
  voting_deadline_at: string;
  escalation_reason: string | null;
  decided_at: string | null;
  created_at: string;
  updated_at: string;
};

export type Vote = {
  session_id: string;
  round: number;
  verdict: "approve" | "reject" | "abstain";
  confidence: number;
  rationale: string;
  request_version: number;
  created_at: string;
  revealed_at: string | null;
};
export type VotesPage = Page<Vote> & { cast: number; quorum: number };

export type StepStatus = "pending" | "approved" | "rejected" | "timed_out" | "superseded";
export type Step = {
  id: string;
  request_version: number;
  level: number;
  required_role: string;
  status: StepStatus;
  due_at: string;
  decided_at: string | null;
  decided_by: string | null;
  stands_in_for_level: number | null;
  created_at: string;
};
export type StepsPage = Page<Step> & { levels: { level: number; role: string; timeout_minutes: number }[]; quorum: number };

export type PendingStep = {
  id: string;
  decision_request_id: string;
  request_version: number;
  level: number;
  required_role: string;
  due_at: string;
  title: string;
  effective_risk_level: Risk;
  requested_by_agent_id: string | null;
};

export type Agent = {
  id: string;
  name: string;
  status: string;
  owner_user_id: string | null;
  created_at: string;
  revoked_at?: string | null;
  [k: string]: unknown;
};

export const ROLE_LABEL: Record<string, string> = {
  member: "Member",
  team_lead: "Team lead",
  manager: "Manager",
  org_admin: "Org admin",
};
export const roleLabel = (role: string) => ROLE_LABEL[role] ?? role;
