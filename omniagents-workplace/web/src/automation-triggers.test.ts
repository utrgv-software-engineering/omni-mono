import { describe, expect, it } from "vitest";
import { describeCron, describeTrigger, scheduleName } from "./automation-triggers";

describe("describeTrigger", () => {
  it("says what starts an automation in words, with its filters", () => {
    expect(
      describeTrigger({
        provider: "github",
        event: "pull_request_opened",
        filter: { repos: ["*/api-service"], base_branches: ["main"], labels: { not_in: ["wip", "factory:Acme"] } },
      }),
    ).toBe("A pull request is opened in */api-service, into main, without the labels wip, factory:Acme");
    expect(describeTrigger({ provider: "slack", event: "reaction_added", filter: { channels: ["factory-intake"], emojis: ["ticket"] } }))
      .toBe("Someone reacts to a Slack message in #factory-intake, with :ticket:");
    expect(describeTrigger({ provider: "github", event: "issue_labeled", filter: { repos: ["*/webapp"], labels: ["needs-repro"] } }))
      .toBe("An issue is labelled in */webapp, labelled needs-repro");
    expect(describeTrigger({ provider: "custom", event: "thing_happened" })).toBe("custom: thing_happened");
  });

  it("reads common schedules and names the schedule to run", () => {
    expect(describeCron("0 2 * * *")).toBe("Every day at 02:00 (server time)");
    expect(describeCron("0 9 * * 1")).toBe("Every Monday at 09:00 (server time)");
    expect(describeCron("*/15 * * * *")).toBe("On the schedule */15 * * * *");
    expect(scheduleName([{ provider: "github", event: "push" }, { provider: "schedule", event: "cron_fired", schedule: { name: "nightly-review", cron: "0 2 * * *" } }]))
      .toBe("nightly-review");
  });
});
