export type JobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "retrying";

export interface AnalyticsOperationSpec {
  id: string;
  name: string;
  description: string;
  category: "descriptive" | "aggregation" | "transformation" | "statistics" | "quality";
  parameters_schema: Record<string, unknown>;
  supported_column_types: string[];
  produces_tabular_output: boolean;
  version: string;
}

export interface AnalyticsJob {
  id: string;
  organization_id: string;
  workspace_id: string;
  source_workbook_id: string;
  source_sheet_id: string;
  operation_id: string;
  parameters: Record<string, unknown>;
  status: JobStatus;
  progress_percent: number;
  current_stage: string | null;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_message: string | null;
  attempt_count: number;
  max_attempts: number;
  result_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface AnalyticsResult {
  id: string;
  job_id: string;
  organization_id: string;
  result_type: string;
  result_payload: Record<string, unknown>;
  row_count: number;
  column_count: number;
  artifact_path: string | null;
  artifact_checksum_sha256: string | null;
  created_at: string;
}

export interface CreateJobInput {
  source_workbook_id: string;
  source_sheet_id: string;
  operation_id: string;
  parameters: Record<string, unknown>;
  idempotency_key?: string;
}

export interface PublishToSheetInput {
  destination_workbook_id: string;
  sheet_name: string;
}
