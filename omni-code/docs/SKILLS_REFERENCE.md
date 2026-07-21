# Skills Reference (Greenfield Guide)

This document is a greenfield playbook for building “file-manipulation” skills like `docx`, `xlsx`, `pptx`, and `pdf`.

## Goal

Create skills that are:
- Easy to trigger (clear metadata)
- Low-context (progressive disclosure)
- Deterministic (scripts over re-generated code)
- Safe (validation + QA loops are mandatory)
- Template-preserving (don’t “beautify” existing docs unless asked)

## What a Skill Is

A skill is a folder containing:
- `SKILL.md` (required)
- Optional bundled resources:
  - `scripts/` (deterministic helpers)
  - `references/` (deep docs loaded only when needed)
  - `assets/` (templates/boilerplate used in outputs)

### Progressive Disclosure (Context Budget)

Design around three loading levels:
1. Metadata: `name` + `description` (always visible; primary trigger)
2. `SKILL.md` body (loaded when skill triggers)
3. Bundled resources (read/run only when needed)

## Start From Real User Prompts

Collect ~10–20 concrete prompts that represent real usage.
Examples:
- “Replace X with Y in this `.docx` and keep tracked changes.”
- “Add a new tab to this workbook, add formulas, and deliver an `.xlsx`.”
- “Update this deck using the same template and layouts.”
- “Merge these PDFs and OCR page 3.”

From these prompts, define:
- What should trigger the skill
- The “golden path” workflow
- Common forks/variants that deserve references and/or scripts

## Metadata: Make Triggering Reliable

In `SKILL.md` YAML frontmatter, only include:
- `name`
- `description`

Write `description` as the routing contract. Include:
- File extensions and synonyms (e.g., “Word doc”, “PowerPoint deck”)
- Typical task verbs (“create”, “edit”, “merge”, “extract”, “fill form”)
- Explicit “use when …” conditions

## What Goes Where

### `SKILL.md` body
Keep it small and operational:
- A quick reference table (Read / Create / Edit)
- A numbered golden path
- A short “pitfalls” list
- Links to `references/*` for variants

### `scripts/`
Add scripts when:
- The same fragile code is being rewritten repeatedly
- Determinism matters (packing/repair, coordinate conversion, validation)
- External tools need stable wrapping (LibreOffice, Poppler, OCR)

Script requirements:
- Explicit input/output arguments
- Idempotent behavior where possible
- Clear failures (exit non-zero; print actionable messages)

### `references/`
Put bulky material here:
- API docs, schemas, OOXML details
- “How to” variants and edge cases
- Tables of options / recipes

Rule: avoid deeply nested references. Link all reference files directly from `SKILL.md`.

### `assets/`
Store:
- Templates (starter decks/docs)
- Boilerplate projects
- Brand assets used in outputs

## Standard Workflow Pattern (All File Skills)

Every format should provide a stable pipeline:

1) Inspect
- Extract text/structure and get a quick overview

2) Transform
- Apply the requested edits using the preferred method

3) Validate
- Run automated checks that detect corruption / schema errors / formula errors

4) QA
- Content QA (missing placeholders, wrong ordering)
- Visual QA when layout matters

Make “Validate + QA” required steps in the skill instructions.

## Format-Specific Golden Paths

### DOCX (OOXML ZIP)
Recommended pipeline:
- Unpack to folder
- Edit XML (and related rels/media as needed)
- Pack back to `.docx`
- Validate

Capabilities to explicitly support:
- Text extraction (optionally including tracked changes)
- Find/replace or structured edits in `word/document.xml`
- Comments + tracked changes workflows
- Image insert/replace via `word/media/` + relationships

Operational guardrails (document as rules):
- Prefer full `unpack → edit → pack` over ad-hoc binary edits
- Smart quotes: when injecting new quoted text, use XML entities
- Tracked changes: don’t nest change tags incorrectly; preserve run properties

### XLSX (Spreadsheet)
Recommended pipeline:
- Read/analyze data (pandas) when needed
- Edit workbook (openpyxl) for formatting and formulas
- Recalculate formulas (LibreOffice-driven) after any formula edits
- Scan for and eliminate Excel error values (#REF!, #DIV/0!, etc.)

Non-negotiable rules:
- Prefer Excel formulas over hardcoded computed values
- Deliver with zero formula errors
- Preserve established template conventions; do not impose new styling

### PPTX (OOXML ZIP)
Two supported modes:

A) Template-based editing (preferred when a template exists)
- Inspect template (thumbnail grid + extracted text)
- Unpack
- Perform structural edits (delete/duplicate/reorder slides)
- Edit slide XML content
- Clean orphaned assets
- Pack + validate
- Visual QA via rendered images

B) Create-from-scratch (when no template exists)
- Use a generation library (e.g., PptxGenJS)
- Build consistently styled slides
- QA for layout and placeholder artifacts

Operational guardrails:
- Avoid monotonous layouts; map content to varied templates
- Avoid Unicode bullets; use proper list/bullet constructs
- Visual QA is mandatory (overlaps and text overflow are common)

### PDF
Core operations:
- Extract text/tables
- Merge/split/rotate/watermark
- OCR scanned pages

PDF form filling must branch:

A) Fillable forms
- Detect fields
- Extract field IDs/options
- Produce `field_values.json`
- Fill with a validated script

B) Non-fillable forms
- Extract structure when possible (preferred)
- Otherwise estimate coordinates via image conversion and zoom refinement
- Validate bounding boxes
- Add annotations/text overlays
- Verify output by rendering pages to images

Operational guardrails:
- Always verify filled form placement visually (rendered images)
- Validate bounding boxes before writing

## Validation and QA (Required)

### Content QA
Add a content-check step per format, for example:
- Grep for placeholder strings (lorem/xxxx/template text)
- Confirm section ordering and completeness

### Visual QA
Required when layout rendering can change:
- PPTX: render slides to images and inspect overlaps/cutoffs
- PDF: render pages to images and inspect placement
- DOCX: optionally convert to PDF/images for proofing

### Verification loop
Document the expected loop:
- Generate → Validate → QA → Fix → Re-validate → Re-QA

Do not claim completion without at least one fix-and-verify cycle on non-trivial outputs.

## Pitfalls to Encode as Explicit Rules

Common “skills like these” failure modes to encode early:
- Unicode bullets cause double-bullets or broken list formatting
- Smart quotes get mangled when editing XML via generic editors
- OOXML pack/unpack without repair/validation silently corrupts files
- PPTX text overflow/overlap only shows up visually
- Spreadsheet formulas aren’t recalculated unless forced; errors slip through
- Reformatting an existing template annoys users; preserve conventions

## Developer Checklist

### Before implementation
- [ ] 10–20 real prompts collected
- [ ] Triggering `description` drafted and reviewed
- [ ] Golden paths defined per prompt cluster

### During implementation
- [ ] Minimal `SKILL.md` body (quick reference + golden path)
- [ ] Scripts for unpack/pack/validate and other deterministic steps
- [ ] References for variants and deep technical notes
- [ ] Assets for templates/boilerplate if needed

### Before shipping
- [ ] Validation is automated and documented
- [ ] QA is explicit and repeatable
- [ ] Representative workflows tested end-to-end
- [ ] Packaged and distributable

## Suggested `SKILL.md` Skeleton

```markdown
---
name: <skill-name>
description: <use when... triggers, file extensions, key tasks>
---

# <Skill Title>

## Quick Reference

| Task | Command / Approach |
|------|---------------------|
| Read / extract | ... |
| Create | ... |
| Edit | ... |
| Validate | ... |
| QA | ... |

## Golden Path

1. Inspect: ...
2. Transform: ...
3. Validate: ...
4. QA: ...

## Variants

- If <case A>: See `references/<A>.md`
- If <case B>: See `references/<B>.md`

## Pitfalls

- ...
```

## Packaging and Iteration

- Initialize skill scaffolding with `init_skill.py` (new skills)
- Package/validate with `package_skill.py`
- Iterate based on real usage: add scripts for repeated pain, move bulk into references, tighten trigger wording
