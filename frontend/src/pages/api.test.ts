import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { act, render, cleanup } from "@testing-library/react";
import { getSkills, submitTask, listTasks, getTaskStatus, getTaskResult } from "../api/client";

beforeEach(() => {
  cleanup();
});

afterEach(() => {
  cleanup();
});

describe("API client", () => {
  it("imports are available", () => {
    expect(getSkills).toBeDefined();
    expect(submitTask).toBeDefined();
    expect(listTasks).toBeDefined();
    expect(getTaskStatus).toBeDefined();
    expect(getTaskResult).toBeDefined();
  });

  it("getSkills function exists and is callable", async () => {
    expect(typeof getSkills).toBe("function");
  });

  it("submitTask function exists and is callable", async () => {
    expect(typeof submitTask).toBe("function");
  });

  it("listTasks function exists and is callable", async () => {
    expect(typeof listTasks).toBe("function");
  });

  it("getTaskStatus function exists and is callable", async () => {
    expect(typeof getTaskStatus).toBe("function");
  });

  it("getTaskResult function exists and is callable", async () => {
    expect(typeof getTaskResult).toBe("function");
  });
});