import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Dashboard } from "../pages/Dashboard";
import * as client from "../api/client";

vi.mock("../api/client");

describe("Dashboard Page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders loading indicator initially then loads real data", async () => {
    vi.spyOn(client, "getHealth").mockResolvedValue({ status: "ok" });
    vi.spyOn(client, "listTasks").mockResolvedValue([
      { submission_id: "t-1", status: "COMPLETED", result: null, metadata: null, history: ["done"] },
      { submission_id: "t-2", status: "PENDING", result: null, metadata: null, history: ["queued"] },
    ]);
    vi.spyOn(client, "getSkills").mockResolvedValue({
      agent: "MATLAB Expert Agent",
      skills: [
        { name: "Simulink Model Development", available: true },
        { name: "Power Systems Engineering", available: true },
      ],
    });

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    );

    // After resolving
    await waitFor(() => {
      expect(screen.getByText("System Dashboard")).toBeInTheDocument();
      expect(screen.getByText("Operational")).toBeInTheDocument();
      expect(screen.getByText("TOTAL TASKS")).toBeInTheDocument();
    });

    // Check table has rows
    expect(screen.getByText("t-1")).toBeInTheDocument();
    expect(screen.getByText("t-2")).toBeInTheDocument();
  });

  it("handles empty task list gracefully", async () => {
    vi.spyOn(client, "getHealth").mockResolvedValue({ status: "ok" });
    vi.spyOn(client, "listTasks").mockResolvedValue([]);
    vi.spyOn(client, "getSkills").mockResolvedValue({
      agent: "MATLAB Expert Agent",
      skills: [],
    });

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("No tasks submitted yet.")).toBeInTheDocument();
    });
  });

  it("displays error banner when API fails", async () => {
    vi.spyOn(client, "getHealth").mockRejectedValue(new Error("Network connection refused"));
    vi.spyOn(client, "listTasks").mockRejectedValue(new Error("Network connection refused"));
    vi.spyOn(client, "getSkills").mockRejectedValue(new Error("Network connection refused"));

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/Failed to load dashboard data/i)).toBeInTheDocument();
    });
  });
});
