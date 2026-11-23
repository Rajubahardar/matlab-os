/* API response types for the Local AI OS V1 frontend.
 * Must match the FastAPI backend implementation in matlab_os/web/api.py.
 */

export interface TaskSubmitRequest {
  description: string;
  metadata?: Record<string, any> | null;
}

export interface TaskInfo {
  submission_id: string;
  status: string;
  result?: any | null;
  metadata?: Record<string, any> | null;
  history: string[];
}

export interface HealthCheck {
  status: string;
}

export interface TaskListItem {
  submission_id: string;
  status: string;
  result?: any | null;
  metadata?: Record<string, any> | null;
  history: string[];
}

export interface TaskListResponse {
  tasks: TaskListItem[];
}

export interface SkillEntry {
  name: string;
  description?: string;
  available?: boolean;
}

export interface SkillsResponse {
  agent: string;
  skills: SkillEntry[];
}
