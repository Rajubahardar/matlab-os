import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "../App";
import * as client from "../api/client";

vi.mock("../api/client");

describe("App Shell & Routing", () => {
  it("renders app bar, brand name, and navigation links", async () => {
    vi.spyOn(client, "getHealth").mockResolvedValue({ status: "ok" });
    vi.spyOn(client, "listTasks").mockResolvedValue([]);
    vi.spyOn(client, "getSkills").mockResolvedValue({ agent: "MATLAB Expert Agent", skills: [] });

    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>
    );

    expect(screen.getByText("Local AI OS V1")).toBeInTheDocument();
    expect(screen.getByText("Engineering AI Workspace")).toBeInTheDocument();
    expect(screen.getByText("Dashboard")).toBeInTheDocument();
    expect(screen.getByText("AI Workspace")).toBeInTheDocument();
    expect(screen.getByText("Engineering Skills")).toBeInTheDocument();
    expect(screen.getByText("Agents")).toBeInTheDocument();
    expect(screen.getByText("Approvals")).toBeInTheDocument();
    expect(screen.getByText("Settings")).toBeInTheDocument();
  });

  it("renders workspace page on /workspace route", async () => {
    vi.spyOn(client, "getHealth").mockResolvedValue({ status: "ok" });

    render(
      <MemoryRouter initialEntries={["/workspace"]}>
        <App />
      </MemoryRouter>
    );

    expect(screen.getByText("Task Submission")).toBeInTheDocument();
  });

  it("renders engineering page on /engineering route", async () => {
    vi.spyOn(client, "getHealth").mockResolvedValue({ status: "ok" });
    vi.spyOn(client, "getSkills").mockResolvedValue({ agent: "MATLAB Expert Agent", skills: [] });

    render(
      <MemoryRouter initialEntries={["/engineering"]}>
        <App />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Engineering Skills Registry")).toBeInTheDocument();
    });
  });
});
