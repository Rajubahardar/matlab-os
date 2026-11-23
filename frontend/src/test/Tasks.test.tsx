import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Tasks } from "../pages/Tasks";
import * as client from "../api/client";

vi.mock("../api/client");

describe("Tasks Page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders tasks table with fetched records", async () => {
    vi.spyOn(client, "listTasks").mockResolvedValue([
      {
        submission_id: "task-abc",
        status: "COMPLETED",
        result: { value: 42 },
        metadata: null,
        history: ["Task submitted", "Completed successfully"],
      },
    ]);

    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("task-abc")).toBeInTheDocument();
      expect(screen.getByText("COMPLETED")).toBeInTheDocument();
      expect(screen.getByText("Completed successfully")).toBeInTheDocument();
    });
  });

  it("handles empty task list", async () => {
    vi.spyOn(client, "listTasks").mockResolvedValue([]);

    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("No tasks match the selected filter.")).toBeInTheDocument();
    });
  });

  it("triggers filter reload on status button click", async () => {
    vi.spyOn(client, "listTasks").mockResolvedValue([]);

    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(client.listTasks).toHaveBeenCalled();
    });

    const completedBtn = screen.getByRole("button", { name: "Completed" });
    fireEvent.click(completedBtn);

    await waitFor(() => {
      expect(client.listTasks).toHaveBeenCalledWith("COMPLETED");
    });
  });
});
