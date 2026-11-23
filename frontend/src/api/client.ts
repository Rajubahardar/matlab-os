/* API client for Local AI OS V1 frontend.
 * Centralized typed API client with proper error handling.
 */

import axios, { AxiosInstance, AxiosError } from "axios";
import { HealthCheck, TaskInfo, TaskListResponse, SkillsResponse } from "./types";

export const api: AxiosInstance = axios.create({
  baseURL: "/api/v1",
  timeout: 10000,
  headers: {
    "Content-Type": "application/json",
  },
});

/* Health check - mounted at root /health */
export async function getHealth(): Promise<HealthCheck> {
  const response = await axios.get<HealthCheck>("/health", { timeout: 5000 });
  return response.data;
}

/* Task submission - POST /api/v1/tasks */
export async function submitTask(
  description: string,
  metadata?: Record<string, any> | null
): Promise<TaskInfo> {
  const response = await api.post<TaskInfo>("/tasks", {
    description,
    metadata,
  });
  return response.data;
}

/* Task status - GET /api/v1/tasks/{submission_id} */
export async function getTaskStatus(submissionId: string): Promise<TaskInfo> {
  const response = await api.get<TaskInfo>(`/tasks/${submissionId}`);
  return response.data;
}

/* Explicit task execution trigger - POST /api/v1/tasks/{submission_id}/execute */
export async function executeTask(submissionId: string): Promise<TaskInfo> {
  const response = await api.post<TaskInfo>(`/tasks/${submissionId}/execute`);
  return response.data;
}

/* Task result - GET /api/v1/tasks/{submission_id}/result */
export async function getTaskResult(submissionId: string): Promise<any> {
  const response = await api.get<any>(`/tasks/${submissionId}/result`);
  return response.data;
}

/* List tasks - GET /api/v1/tasks */
export async function listTasks(status?: string): Promise<TaskInfo[]> {
  const params: Record<string, string> = {};
  if (status) {
    params.status = status;
  }
  const response = await api.get<TaskInfo[]>("/tasks", { params });
  return response.data;
}

/* Skills - GET /api/v1/skills */
export async function getSkills(): Promise<SkillsResponse> {
  const response = await api.get<SkillsResponse>("/skills");
  return response.data;
}

/* Approvals - GET /api/v1/approvals */
export async function getApprovals(): Promise<any[]> {
  const response = await api.get<any[]>("/approvals");
  return response.data;
}

/* Response error interceptor */
api.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    if (error.response) {
      const message = (error.response.data as any)?.detail || error.response.statusText || "Request failed";
      throw new Error(`HTTP ${error.response.status}: ${message}`);
    } else if (error.request) {
      throw new Error("Network error: backend unreachable");
    } else {
      throw new Error(error.message || "Request failed");
    }
  }
);
