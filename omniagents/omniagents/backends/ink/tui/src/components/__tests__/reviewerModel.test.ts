import { describe, expect, it } from "vitest";
import {
  APPROVALS_CONFIG_KEY,
  CHECKS_CONFIG_KEY,
  configFieldValue,
  DEFAULT_APPROVALS_REVIEWER,
  DEFAULT_WORKFLOW_REVIEWER,
  guardianReviewLine,
  parseGuardianReview,
  parseWorkflowReview,
  resolveReviewer,
  sourceNote,
  workflowReviewLine,
  type ApprovalsReviewer,
  type WorkflowReviewer,
} from "../reviewer-model.js";

const APPROVALS: readonly ApprovalsReviewer[] = ["user", "auto"];
const CHECKS: readonly WorkflowReviewer[] = ["off", "guardian"];

describe("resolveReviewer", () => {
  it("prefers the session override", () => {
    expect(
      resolveReviewer("auto", "user", DEFAULT_APPROVALS_REVIEWER, APPROVALS),
    ).toEqual({ value: "auto", source: "session" });
  });

  it("falls back to config when there is no session override", () => {
    // The case Desktop's hardcoded useState('user') gets wrong: config
    // says auto, so the guardian is answering, and a switch that renders
    // "Ask me first" is lying about its own position.
    expect(
      resolveReviewer(null, "auto", DEFAULT_APPROVALS_REVIEWER, APPROVALS),
    ).toEqual({ value: "auto", source: "config" });
  });

  it("falls back to the built-in default when neither is set", () => {
    expect(
      resolveReviewer(null, undefined, DEFAULT_APPROVALS_REVIEWER, APPROVALS),
    ).toEqual({ value: "user", source: "default" });
    expect(
      resolveReviewer(null, undefined, DEFAULT_WORKFLOW_REVIEWER, CHECKS),
    ).toEqual({ value: "guardian", source: "default" });
  });

  it("ignores values outside the allowed set", () => {
    expect(
      resolveReviewer("nonsense", 42, DEFAULT_APPROVALS_REVIEWER, APPROVALS),
    ).toEqual({ value: "user", source: "default" });
  });

  it("names where the value came from", () => {
    expect(sourceNote("session")).toContain("conversation");
    expect(sourceNote("config")).toContain("configuration");
    expect(sourceNote("default")).toBe("default");
  });
});

describe("configFieldValue", () => {
  const config = {
    fields: [
      { key: APPROVALS_CONFIG_KEY, value: "auto", is_set: true },
      { key: CHECKS_CONFIG_KEY, value: "off", is_set: false },
      { key: "other.key", value: "x", is_set: true },
    ],
  };

  it("reads a set field's effective value", () => {
    expect(configFieldValue(config, APPROVALS_CONFIG_KEY)).toBe("auto");
  });

  it("treats an unset field as absent rather than as its null value", () => {
    expect(configFieldValue(config, CHECKS_CONFIG_KEY)).toBeUndefined();
  });

  it("returns undefined for a missing field or malformed document", () => {
    expect(configFieldValue(config, "nope")).toBeUndefined();
    expect(configFieldValue(undefined, APPROVALS_CONFIG_KEY)).toBeUndefined();
    expect(
      configFieldValue({ fields: "nope" }, APPROVALS_CONFIG_KEY),
    ).toBeUndefined();
    expect(
      configFieldValue({ fields: [null] }, APPROVALS_CONFIG_KEY),
    ).toBeUndefined();
  });
});

describe("parseGuardianReview", () => {
  it("reads a well-formed payload", () => {
    expect(
      parseGuardianReview({
        call_id: "c1",
        tool_name: "write_file",
        outcome: "deny",
        reviewer: "guardian",
        risk_level: "high",
        rationale: "writes outside the workspace",
      }),
    ).toEqual({
      callId: "c1",
      tool: "write_file",
      reviewer: "guardian",
      outcome: "deny",
      kind: "tool",
      serverLabel: undefined,
      riskLevel: "high",
      rationale: "writes outside the workspace",
    });
  });

  it("rejects payloads with no call id or no usable outcome", () => {
    expect(parseGuardianReview({ outcome: "allow" })).toBeNull();
    expect(parseGuardianReview({ call_id: "c1", outcome: "maybe" })).toBeNull();
    expect(parseGuardianReview(undefined)).toBeNull();
  });

  it("keeps the mcp kind and its server label", () => {
    const review = parseGuardianReview({
      call_id: "c1",
      outcome: "allow",
      kind: "mcp",
      server_label: "github",
      tool_name: "create_issue",
    });
    expect(review?.kind).toBe("mcp");
    expect(review?.serverLabel).toBe("github");
  });
});

describe("guardianReviewLine", () => {
  it("names the tool, the verdict and the reviewer", () => {
    expect(
      guardianReviewLine({
        callId: "c",
        tool: "write_file",
        reviewer: "guardian",
        outcome: "deny",
      }),
    ).toBe("write_file denied by guardian");
  });

  it("spells sandbox-policy the way a person would read it", () => {
    expect(
      guardianReviewLine({
        callId: "c",
        tool: "bash",
        reviewer: "sandbox-policy",
        outcome: "allow",
      }),
    ).toBe("bash approved by sandbox policy");
  });

  it("qualifies an mcp tool with its server", () => {
    expect(
      guardianReviewLine({
        callId: "c",
        tool: "create_issue",
        serverLabel: "github",
        reviewer: "guardian",
        outcome: "allow",
      }),
    ).toBe("github · create_issue approved by guardian");
  });

  it("leaves the rationale off the line", () => {
    // It renders on its own row under a blocking review. Concatenated,
    // it was simply truncated off the end of the line instead.
    expect(
      guardianReviewLine({
        callId: "c",
        tool: "bash",
        reviewer: "guardian",
        outcome: "deny",
        rationale: "deletes the repo",
      }),
    ).toBe("bash denied by guardian");
  });
});

describe("parseWorkflowReview / workflowReviewLine", () => {
  it("reads each outcome the server broadcasts", () => {
    for (const outcome of [
      "reject",
      "accept_unverified",
      "escalated",
      "accept_verified",
    ]) {
      expect(
        parseWorkflowReview({ task_id: "3", outcome, subject: "s" })?.outcome,
      ).toBe(outcome);
    }
  });

  it("rejects an unusable payload", () => {
    expect(parseWorkflowReview({ outcome: "reject" })).toBeNull();
    expect(parseWorkflowReview({ task_id: "3", outcome: "shrug" })).toBeNull();
  });

  it("keeps the rationale off the line", () => {
    // A rejection renders the rationale on its own row; the summary line
    // stays one scannable sentence.
    expect(
      workflowReviewLine({
        taskId: "3",
        subject: "add tests",
        reviewer: "guardian",
        outcome: "reject",
        rationale: "no assertions added",
      }),
    ).toBe("step #3 'add tests' — completion rejected");
  });

  it("words each outcome distinctly", () => {
    const base = {
      taskId: "1",
      subject: "s",
      reviewer: "guardian",
    } as const;
    expect(
      workflowReviewLine({ ...base, outcome: "accept_verified" }),
    ).toContain("completion verified");
    expect(
      workflowReviewLine({ ...base, outcome: "accept_unverified" }),
    ).toContain("accepted (unverified)");
    expect(workflowReviewLine({ ...base, outcome: "escalated" })).toContain(
      "repeated disagreement",
    );
  });
});
