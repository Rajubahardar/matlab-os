import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AppLayout } from "../components/AppLayout";
import { AIWorkspace } from "./AIWorkspace";
import { Dashboard } from "./Dashboard";
import { Engineering } from "./Engineering";
import { Tasks } from "./Tasks";
import { Settings } from "./Settings";
import { Agents } from "./Agents";

describe("App navigation", () => {
  it("renders AppLayout component", () => {
    const { container } = render(<AppLayout><AIWorkspace /></AppLayout>);
    expect(container.querySelector("h5")).toBeInTheDocument();
  });

  it("renders Dashboard page", () => {
    const { container } = render(<AppLayout><Dashboard /></AppLayout>);
    const header = container.querySelector("h5");
    expect(header).toBeInTheDocument();
    expect(header?.textContent).toContain("Dashboard");
  });

  it("renders Engineering page", () => {
    const { container } = render(<AppLayout><Engineering /></AppLayout>);
    const header = container.querySelector("h5");
    expect(header).toBeInTheDocument();
    expect(header?.textContent).toContain("Engineering");
  });

  it("renders Tasks page", () => {
    const { container } = render(<AppLayout><Tasks /></AppLayout>);
    const header = container.querySelector("h5");
    expect(header).toBeInTheDocument();
    expect(header?.textContent).toContain("Tasks");
  });

  it("renders Settings page", () => {
    const { container } = render(<AppLayout><Settings /></AppLayout>);
    const header = container.querySelector("h5");
    expect(header).toBeInTheDocument();
    expect(header?.textContent).toContain("Settings");
  });

  it("renders Agents page", () => {
    const { container } = render(<AppLayout><Agents /></AppLayout>);
    const header = container.querySelector("h5");
    expect(header).toBeInTheDocument();
    expect(header?.textContent).toContain("Agents");
  });
});