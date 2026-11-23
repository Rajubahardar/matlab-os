import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Engineering } from "../pages/Engineering";
import * as client from "../api/client";

vi.mock("../api/client");

describe("Engineering Skills Page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders skills list from backend", async () => {
    vi.spyOn(client, "getSkills").mockResolvedValue({
      agent: "MATLAB Expert Agent",
      skills: [
        { name: "MATLAB Programming and Debugging", description: "Core coding" },
        { name: "Simulink Model Development", description: "Dynamic systems" },
        { name: "Power Systems Engineering", description: "Grid analysis" },
      ],
    });

    render(
      <MemoryRouter>
        <Engineering />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Engineering Skills Registry")).toBeInTheDocument();
      expect(screen.getByText("MATLAB Programming and Debugging")).toBeInTheDocument();
      expect(screen.getByText("Simulink Model Development")).toBeInTheDocument();
      expect(screen.getByText("Power Systems Engineering")).toBeInTheDocument();
    });
  });

  it("handles skill load failure", async () => {
    vi.spyOn(client, "getSkills").mockRejectedValue(new Error("Failed to load skills"));

    render(
      <MemoryRouter>
        <Engineering />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/Failed to load skills/i)).toBeInTheDocument();
    });
  });
});
