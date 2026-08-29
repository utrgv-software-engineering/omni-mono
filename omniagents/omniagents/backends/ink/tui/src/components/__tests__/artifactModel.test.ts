import { describe, expect, it } from "vitest";
import {
  artifactBytes,
  artifactMeta,
  base64Bytes,
  defaultFileName,
  extensionFor,
  formatBytes,
  isBinary,
  parseDataUrl,
  previewLines,
  saveBuffer,
  slugify,
  upsertArtifact,
  type ArtifactEntry,
} from "../artifact-model.js";

function entry(overrides: Partial<ArtifactEntry> = {}): ArtifactEntry {
  return {
    artifactId: "a-1",
    title: "Report",
    content: "# Heading\n\nbody",
    mode: "markdown",
    sessionId: "s-1",
    updatedAt: 1_700_000_000_000,
    revision: 0,
    ...overrides,
  };
}

/** `data:image/png;base64,` + "hello" */
const PNG_DATA_URL = "data:image/png;base64,aGVsbG8=";

describe("parseDataUrl / isBinary", () => {
  it("splits mime and payload", () => {
    expect(parseDataUrl(PNG_DATA_URL)).toEqual({
      mime: "image/png",
      base64: "aGVsbG8=",
    });
  });

  it("returns null for ordinary text", () => {
    expect(parseDataUrl("# not a data url")).toBeNull();
    expect(parseDataUrl("data:image/png,notbase64")).toBeNull();
  });

  it("defaults a missing mime rather than throwing", () => {
    expect(parseDataUrl("data:;base64,aGk=")?.mime).toBe(
      "application/octet-stream",
    );
  });

  it("classifies from the PAYLOAD, not the declared mode", () => {
    // The regression this guards: trusting `mode` would dump a megabyte
    // of base64 into the TTY when a text artifact is mislabelled, and
    // refuse to preview a real document mislabelled the other way.
    expect(isBinary(entry({ mode: "image", content: "actually text" }))).toBe(
      false,
    );
    expect(isBinary(entry({ mode: "markdown", content: PNG_DATA_URL }))).toBe(
      true,
    );
  });
});

describe("sizes", () => {
  it("computes decoded length without decoding", () => {
    expect(base64Bytes("aGVsbG8=")).toBe(5);
    expect(base64Bytes("aGVsbG8h")).toBe(6);
    expect(base64Bytes("YQ==")).toBe(1);
    expect(base64Bytes("")).toBe(0);
  });

  it("measures binary decoded and text as utf-8", () => {
    expect(artifactBytes(entry({ content: PNG_DATA_URL }))).toBe(5);
    expect(artifactBytes(entry({ content: "abc" }))).toBe(3);
    expect(artifactBytes(entry({ content: "é" }))).toBe(2);
  });

  it("formats at sensible thresholds", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});

describe("upsertArtifact", () => {
  it("appends a new id", () => {
    const list = upsertArtifact([], entry());
    expect(list).toHaveLength(1);
  });

  it("replaces the same id and bumps the revision", () => {
    const first = upsertArtifact([], entry());
    const second = upsertArtifact(first, entry({ content: "changed" }));
    expect(second).toHaveLength(1);
    expect(second[0]!.content).toBe("changed");
    expect(second[0]!.revision).toBe(1);
  });

  it("always appends when there is no id to address", () => {
    const list = upsertArtifact(
      upsertArtifact([], entry({ artifactId: undefined })),
      entry({ artifactId: undefined }),
    );
    expect(list).toHaveLength(2);
  });

  it("keeps artifacts from different sessions apart", () => {
    const first = upsertArtifact([], entry({ sessionId: "s-1" }));
    const second = upsertArtifact(first, entry({ sessionId: "s-2" }));
    expect(second).toHaveLength(2);
  });

  it("returns the same reference when nothing changed", () => {
    const first = upsertArtifact([], entry());
    expect(upsertArtifact(first, entry())).toBe(first);
  });

  it("does not mutate the input list", () => {
    const first = upsertArtifact([], entry());
    upsertArtifact(first, entry({ content: "changed" }));
    expect(first[0]!.content).toBe("# Heading\n\nbody");
  });
});

describe("filenames", () => {
  it("prefers the payload mime over the mode", () => {
    expect(
      extensionFor(entry({ mode: "markdown", content: PNG_DATA_URL })),
    ).toBe("png");
    expect(extensionFor(entry({ content: "text", mode: "html" }))).toBe("html");
    expect(extensionFor(entry({ content: "text", mode: "markdown" }))).toBe(
      "md",
    );
  });

  it("falls back for unknown modes and mimes", () => {
    expect(extensionFor(entry({ content: "x", mode: "weird" }))).toBe("txt");
    expect(
      extensionFor(entry({ content: "data:image/tiff;base64,YQ==" })),
    ).toBe("tiff");
    expect(extensionFor(entry({ content: "data:!!!;base64,YQ==" }))).toBe(
      "bin",
    );
  });

  it("refuses to let an agent-authored title choose a path", () => {
    // The title is untrusted text. A traversal in it must not escape the
    // directory the user picked.
    expect(slugify("../../.bashrc")).toBe("bashrc");
    expect(slugify("/etc/passwd")).toBe("etcpasswd");
    expect(
      defaultFileName(
        entry({ title: "../../evil", content: "x", mode: "markdown" }),
      ),
    ).toBe("evil.md");
    expect(slugify("")).toBe("artifact");
    expect(slugify("!!!")).toBe("artifact");
  });

  it("keeps names readable and bounded", () => {
    expect(slugify("My Weekly Report")).toBe("My-Weekly-Report");
    expect(slugify("a".repeat(200)).length).toBe(60);
  });
});

describe("saveBuffer", () => {
  it("decodes binary payloads", () => {
    expect(saveBuffer(entry({ content: PNG_DATA_URL })).toString("utf8")).toBe(
      "hello",
    );
  });

  it("writes text verbatim", () => {
    expect(saveBuffer(entry({ content: "# hi" })).toString("utf8")).toBe(
      "# hi",
    );
  });
});

describe("previewLines", () => {
  it("wraps to the given width", () => {
    expect(previewLines("abcdefghij", 4, 10)).toEqual(["abcd", "efgh", "ij"]);
  });

  it("preserves blank lines and normalizes CRLF", () => {
    expect(previewLines("a\r\n\r\nb", 10, 10)).toEqual(["a", "", "b"]);
  });

  it("strips control characters so content cannot repaint the TUI", () => {
    // An artifact carrying an ANSI escape must not be able to move the
    // cursor or recolor the chrome around it.
    const ansi = "\u001b[31mred\u001b[0m";
    expect(previewLines(ansi, 40, 10)).toEqual(["[31mred[0m"]);
    expect(previewLines("a\u0000b", 40, 10)).toEqual(["ab"]);
    expect(previewLines("a\u007fb", 40, 10)).toEqual(["ab"]);
  });

  it("expands tabs", () => {
    expect(previewLines("a\tb", 40, 10)).toEqual(["a  b"]);
  });

  it("stops at the line limit", () => {
    expect(previewLines("a\nb\nc\nd", 10, 2)).toEqual(["a", "b"]);
  });
});

describe("artifactMeta", () => {
  it("summarizes mode and size", () => {
    expect(artifactMeta(entry({ content: "abc", mode: "markdown" }))).toBe(
      "markdown · 3 B",
    );
  });

  it("names the revision once it has one", () => {
    expect(artifactMeta(entry({ content: "abc", revision: 2 }))).toContain(
      "rev 3",
    );
  });

  it("marks binary payloads", () => {
    expect(artifactMeta(entry({ content: PNG_DATA_URL }))).toContain("binary");
  });
});
