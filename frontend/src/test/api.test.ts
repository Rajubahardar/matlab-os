import { describe, it, expect, vi, beforeEach } from "vitest";
import axios from "axios";
import { getHealth, submitTask, getTaskStatus, getTaskResult, listTasks, getSkills, api } from "../api/client";

vi.mock("axios", async () => {
  const actual = await vi.importActual<any>("axios");
  const mockAxiosInstance = {
    get: vi.fn(),
    post: vi.fn(),
    interceptors: {
      response: {
        use: vi.fn(),
      },
    },
  };
  return {
    default: {
      ...actual.default,
      create: vi.fn(() => mockAxiosInstance),
      get: vi.fn(),
      post: vi.fn(),
    },
    AxiosError: actual.AxiosError,
  };
});

describe("API Client", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("getHealth calls /health and returns data", async () => {
    (axios.get as any).mockResolvedValueOnce({ data: { status: "ok" } });
    const result = await getHealth();
    expect(result).toEqual({ status: "ok" });
    expect(axios.get).toHaveBeenCalledWith("/health", expect.any(Object));
  });

  it("submitTask calls /tasks with description and metadata", async () => {
    const mockData = {
      submission_id: "task-123",
      status: "pending",
      result: null,
      metadata: { priority: "high" },
      history: ["submitted"],
    };
    (api.post as any).mockResolvedValueOnce({ data: mockData });

    const res = await submitTask("Run test simulation", { priority: "high" });
    expect(res).toEqual(mockData);
    expect(api.post).toHaveBeenCalledWith("/tasks", {
      description: "Run test simulation",
      metadata: { priority: "high" },
    });
  });

  it("getTaskStatus calls /tasks/{id}", async () => {
    const mockData = {
      submission_id: "task-123",
      status: "completed",
      result: 4,
      metadata: null,
      history: ["submitted", "completed"],
    };
    (api.get as any).mockResolvedValueOnce({ data: mockData });

    const res = await getTaskStatus("task-123");
    expect(res).toEqual(mockData);
    expect(api.get).toHaveBeenCalledWith("/tasks/task-123");
  });

  it("getTaskResult calls /tasks/{id}/result", async () => {
    const mockResult = { status: "completed", output: 4 };
    (api.get as any).mockResolvedValueOnce({ data: mockResult });

    const res = await getTaskResult("task-123");
    expect(res).toEqual(mockResult);
    expect(api.get).toHaveBeenCalledWith("/tasks/task-123/result");
  });

  it("listTasks calls /tasks with optional status filter", async () => {
    const mockList = [
      { submission_id: "t1", status: "completed", result: null, metadata: null, history: [] },
    ];
    (api.get as any).mockResolvedValueOnce({ data: mockList });

    const res = await listTasks("completed");
    expect(res).toEqual(mockList);
    expect(api.get).toHaveBeenCalledWith("/tasks", { params: { status: "completed" } });
  });

  it("getSkills calls /skills", async () => {
    const mockSkills = {
      agent: "MATLAB Expert Agent",
      skills: [{ name: "Simulink Model Development", description: "Simulink", available: true }],
    };
    (api.get as any).mockResolvedValueOnce({ data: mockSkills });

    const res = await getSkills();
    expect(res).toEqual(mockSkills);
    expect(api.get).toHaveBeenCalledWith("/skills");
  });
});
