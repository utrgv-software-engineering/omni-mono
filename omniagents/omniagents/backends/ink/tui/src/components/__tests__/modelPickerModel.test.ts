import { describe, expect, it } from "vitest";
import {
  buildModelRows,
  initialIndex,
  windowStart,
} from "../model-picker-model.js";
import type { ListModelsResult, ModelDescriptor } from "../../protocol/catalog.js";

function model(overrides: Partial<ModelDescriptor>): ModelDescriptor {
  return {
    id: "anthropic:claude-sonnet-5",
    model: "claude-sonnet-5",
    label: "Sonnet 5",
    description: null,
    provider: { name: "anthropic", type: "anthropic" },
    modalities: ["text"],
    realtime: false,
    limits: { max_input_tokens: null, max_output_tokens: null },
    reasoning: { default: null, options: [] },
    tiers: { service: null, speed: null },
    personality: { supported: false, options: [], default: null },
    availability: { available: true, reasons: [] },
    entitlement: { entitled: true, credential: "api_key" },
    deprecation: { deprecated: false, message: null, replace_with: null },
    hidden: false,
    is_default: false,
    is_voice_default: false,
    is_user_defined: false,
    ...overrides,
  };
}

function listResult(overrides: Partial<ListModelsResult>): ListModelsResult {
  return {
    models: [],
    default_model: null,
    voice_default_model: null,
    errors: [],
    reasons: [],
    ...overrides,
  };
}

describe("buildModelRows", () => {
  it("hides hidden models and resolves the active model session-first", () => {
    const rows = buildModelRows(
      listResult({
        models: [
          model({ id: "a", label: "A" }),
          model({ id: "b", label: "B", hidden: true }),
          model({ id: "c", label: "C" }),
        ],
        default_model: "a",
        session: { session_id: "s", active_model: "c", reasoning_effort: null },
      }),
      "a",
    );
    expect(rows.map((r) => r.id)).toEqual(["a", "c"]);
    expect(rows.find((r) => r.id === "c")?.active).toBe(true);
    expect(rows.find((r) => r.id === "a")?.active).toBe(false);
  });

  it("falls back to the client's known model, then the catalog default", () => {
    const models = [model({ id: "a" }), model({ id: "b" })];
    expect(
      buildModelRows(listResult({ models, default_model: "a" }), "b").find(
        (r) => r.active,
      )?.id,
    ).toBe("b");
    expect(
      buildModelRows(listResult({ models, default_model: "a" }), null).find(
        (r) => r.active,
      )?.id,
    ).toBe("a");
  });

  it("matches the active model by provider-side id too", () => {
    const rows = buildModelRows(
      listResult({
        models: [model({ id: "anthropic:claude-sonnet-5" })],
      }),
      "claude-sonnet-5",
    );
    expect(rows[0]!.active).toBe(true);
  });

  it("surfaces deprecation over availability as the note", () => {
    const rows = buildModelRows(
      listResult({
        models: [
          model({
            id: "old",
            deprecation: {
              deprecated: true,
              message: "use Sonnet 5",
              replace_with: "anthropic:claude-sonnet-5",
            },
            availability: {
              available: false,
              reasons: [{ code: "missing_credentials", message: "no key" }],
            },
          }),
          model({
            id: "locked",
            availability: {
              available: false,
              reasons: [{ code: "oauth_signin_required", message: "sign in" }],
            },
          }),
        ],
      }),
      null,
    );
    expect(rows[0]!.note).toBe("use Sonnet 5");
    expect(rows[1]!.note).toBe("sign in");
    expect(rows[1]!.available).toBe(false);
  });
});

describe("initialIndex", () => {
  it("starts on the active row, else the first", () => {
    const rows = buildModelRows(
      listResult({
        models: [model({ id: "a" }), model({ id: "b" })],
        session: { session_id: "s", active_model: "b", reasoning_effort: null },
      }),
      null,
    );
    expect(initialIndex(rows)).toBe(1);
    expect(initialIndex(buildModelRows(listResult({ models: [] }), null))).toBe(
      0,
    );
  });
});

describe("windowStart", () => {
  it("keeps the cursor visible and clamps at the edges", () => {
    expect(windowStart(0, 20, 8)).toBe(0);
    expect(windowStart(10, 20, 8)).toBe(6);
    expect(windowStart(19, 20, 8)).toBe(12);
    expect(windowStart(3, 5, 8)).toBe(0);
  });
});
