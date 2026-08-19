# Toolbox — Task Queue

A dark, utilitarian collection of self-contained browser tools, served by a thin Flask
shell. Reference look: "Echo Net Extras" — homepage grid of tool cards + persistent
left sidebar, `Ctrl+K` search, dark theme, no marketing chrome.

## Ground rules (every worker reads this)

- **Client-side first.** Flask serves Jinja templates + static assets. Tool logic runs
  in the browser in **vanilla JS** — one self-contained module per tool, no bundler,
  no framework. A tool loads only its own JS + the single library it needs.
- **Stateless.** No accounts, no auth, no deploy, no persistence for new tools. The only
  DB is the existing link extractor's SQLite.
- **Isolation.** Tools never import each other. A broken tool must not affect any other
  tool or the shell.
- **Registration.** A tool is registered in ONE place (`TOOLS` registry — see T3). Adding
  a tool = new module + one registry entry. Do not hand-edit the sidebar or grid.
- **Commit per task.** One task = one commit. Message: `T<n>: <title>`.
- **Every task self-verifies** against its Acceptance + Smoke before commit.

## Dependency graph

```
T1 shell ─┬─> T2 theme ─┬─> T3 tool-template/registry ─┬─> T4  link extractor+
          │             │                              ├─> T5  image converter
          │             │                              ├─> T6  background remover
          │             │                              ├─> T7  PDF toolkit
          │             │                              ├─> T8  JSON/text toolbox
          │             │                              ├─> T9  JSON viewer
          │             │                              ├─> T10 encoder/decoder
          │             │                              └─> T11 QR generator
```
T4–T11 are independent of each other and may run in parallel once T3 lands.

---

## FOUNDATION (sequential — must be correct before any tool starts)

### T1 — Base shell & routing
**Depends on:** none
**Do:** Flask app restructured to serve a base layout: persistent left sidebar +
content pane, dark theme scaffold, homepage grid route (`/`), per-tool route pattern
(`/tools/<slug>`), and an About page. Add `Ctrl+K` to focus a search box that filters
both the sidebar list and homepage grid. Sidebar is collapsible; footer shows a version
string. Keep the existing link-extractor routes working (do not break `/submit`).
**Acceptance:**
- `/` renders a grid; sidebar lists tools; About route renders.
- `Ctrl+K` focuses search; typing filters visible tools.
- Existing link-extractor POST endpoint still returns 200 for a valid submission.
**Smoke:** `flask run`, load `/`, `/about`; verify sidebar + grid + Ctrl+K in browser.

### T2 — Design system / Tailwind theme
**Depends on:** T1
**Do:** Define the dark utilitarian theme in Tailwind (bg near-black, one accent, bold
sans headings, monospace for code/keys, spacing scale, focusable states, keyboard-key
`<kbd>` styling). Build the CSS via the existing `build:css` script. All shell surfaces
adopt it. No per-tool bespoke styling.
**Acceptance:** shell matches reference aesthetic; `npm run build:css` succeeds; no
inline color hex in templates (use theme tokens).
**Smoke:** rebuild CSS, reload, visually confirm dark theme + kbd styling.

### T3 — Tool template + registry convention
**Depends on:** T2
**Do:** Create the mechanism every tool plugs into:
- A `TOOLS` registry (single source of truth: slug, name, description, icon, category).
- Sidebar + homepage grid both render FROM the registry (no hardcoded lists).
- A shared tool-page Jinja template (title, description, content block, per-tool JS
  slot) that a tool extends.
- A short `HOW_TO_ADD_A_TOOL.md` documenting the 2-step convention.
**Acceptance:** adding a dummy registry entry makes it appear in sidebar + grid + routes
to a working page, with zero edits outside the module + registry.
**Smoke:** add throwaway "hello" tool, confirm it appears everywhere, remove it.

---

## TOOLS (parallel — each depends only on T3)

### T4 — Link extractor+ (upgrade of the existing Tool #1)
**Do:** Wrap the existing paste/upload → extract → dedupe → list flow into the new tool
shell. Then ADD features: copy-all, per-link copy, domain grouping, count badge, and
export as Markdown/JSON. Keep server-side extraction + SQLite as-is.
**Acceptance:** existing behavior preserved; at least 3 new features work.
**Smoke:** paste text with mixed markdown + bare URLs → deduped list + export works.

### T5 — Image converter (Canvas)
**Do:** Client-side convert between PNG/JPG/WebP; optional resize + quality/compress.
Drag-drop or file picker; download result. No server upload.
**Acceptance:** load a PNG, convert to JPG+WebP, resize, download each.
**Smoke:** round-trip a test image in browser.

### T6 — Background remover
**Do:** In-browser bg removal via `@imgly/background-removal` (WASM/ONNX, model fetched
on first use). Show progress; output transparent PNG; download. Handle the first-load
model fetch gracefully (loading state).
**Acceptance:** upload a photo → transparent-background PNG downloads. Loading state
shown during model fetch.
**Smoke:** run on a sample portrait in browser.

### T7 — PDF toolkit (`pdf-lib`)
**Do:** Merge multiple PDFs, split/extract pages, images→PDF. All client-side.
**Acceptance:** merge 2 PDFs; extract a page range; build a PDF from images.
**Smoke:** exercise all three in browser with test files.

### T8 — JSON / text toolbox
**Do:** JSON format/prettify, minify, validate (clear error w/ line), plus text diff.
**Acceptance:** valid JSON prettifies; invalid shows a useful error; diff highlights.
**Smoke:** paste malformed JSON → error; paste two texts → diff.

### T9 — JSON viewer
**Do:** Collapsible tree view of JSON with expand/collapse, key search/filter, and
copy-path on a node. (Separate from T8's formatter.)
**Acceptance:** large JSON renders as a navigable tree; search filters nodes.
**Smoke:** paste nested JSON → expand/collapse + search work.

### T10 — Encoder / decoder
**Do:** base64 encode/decode, URL encode/decode, JWT decode (header/payload, no verify),
common hashes (MD5/SHA-1/SHA-256 via SubtleCrypto). Tabbed within one tool page.
**Acceptance:** each mode round-trips / decodes correctly.
**Smoke:** base64 + JWT + SHA-256 in browser.

### T11 — QR code generator
**Do:** Text/URL → QR (client-side lib); adjustable size; download PNG/SVG.
**Acceptance:** generate + download a scannable QR.
**Smoke:** generate a QR for a URL, confirm it scans.

---

## STRETCH (only if the queue empties)
- Color tools (palette + WCAG contrast checker)
- Password / UUID generator
- Submit-feedback link, Collapse-sidebar persistence
