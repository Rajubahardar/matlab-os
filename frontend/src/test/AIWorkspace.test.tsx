import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AIWorkspace } from "../pages/AIWorkspace";
import * as client from "../api/client";

vi.mock("../api/client");

describe("AI Workspace Page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders workspace title, form, and quick task buttons", () => {
    render(
      <MemoryRouter>
        <AIWorkspace />
      </MemoryRouter>
    );

    expect(screen.getByText("AI Workspace")).toBeInTheDocument();
    expect(screen.getByText("Task Submission")).toBeInTheDocument();
    expect(screen.getByText("disp(2+2)")).toBeInTheDocument();
  });

  it("submits task and displays inspector with submission ID", async () => {
    vi.spyOn(client, "submitTask").mockResolvedValue({
      submission_id: "test-sub-999",
      status: "SUBMITTED",
      result: null,
      metadata: null,
      history: ["submitted: disp(2+2)"],
    });

    vi.spyOn(client, "getTaskStatus").mockResolvedValue({
      submission_id: "test-sub-999",
      status: "COMPLETED",
      result: "4",
      metadata: null,
      history: ["submitted: disp(2+2)", "completed"],
    });

    vi.spyOn(client, "getTaskResult").mockResolvedValue({
      status: "COMPLETED",
      output: 4,
    });

    render(
      <MemoryRouter>
        <AIWorkspace />
      </MemoryRouter>
    );

    const input = screen.getByPlaceholderText(/Enter engineering task description/i);
    fireEvent.change(input, { target: { value: "disp(2+2)" } });

    const submitBtn = screen.getByRole("button", { name: /Submit Task/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(client.submitTask).toHaveBeenCalledWith(
        "disp(2+2)",
        expect.any(Object)
      );
      expect(screen.getByText("test-sub-999")).toBeInTheDocument();
      expect(screen.getByText("Task Inspector")).toBeInTheDocument();
    });
  });

  it("handles submission failure with error alert", async () => {
    vi.spyOn(client, "submitTask").mockRejectedValue(new Error("Server error 500"));

    render(
      <MemoryRouter>
        <AIWorkspace />
      </MemoryRouter>
    );

    const input = screen.getByPlaceholderText(/Enter engineering task description/i);
    fireEvent.change(input, { target: { value: "invalid syntax test" } });

    const submitBtn = screen.getByRole("button", { name: /Submit Task/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(screen.getByText(/Server error 500/i)).toBeInTheDocument();
    });
  });
});
