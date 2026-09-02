# VTC — UI-first pass: design notes

Four additions to `vtc/vtc_app_v3.html`, all reviewable on mock data by opening the
file directly in Chrome. No backend wiring — the file feature-detects `pywebview`
and stays on mock data standalone, so every new surface is visible and interactive
without the app shell. Nothing is committed.

## How to review (open the file in Chrome)

`file:///Users/simondavis/projects/very_thoughtful_compression/vtc/vtc_app_v3.html`

1. Click **Choose media folder** → pick any demo folder. The guided flow appears.
2. **Two new steps** are now in the comb (7 teeth): **RESIZE** (step 3, after Quality)
   and **IGNORE** (step 7, last). Click a tooth to jump to it. On RESIZE, pick
   **Custom max long-edge** to reveal the inline px box. On IGNORE, pick **Custom…**
   to reveal the full filter fields.
3. **Utilities** — top-right, the quiet **Compress / Utilities** segment. Click
   **Utilities**: pick a tool → it scans (mock) → act per file → **Run** hands off to
   the shared progress sheet, then the shared report sheet.
4. **Theme** — top-right, the **Auto / Light / Dark** button (sun/moon/half-disc glyph
   with a label). Click to cycle; the choice persists (localStorage). Fuller picker
   also in **Advanced → Appearance**. Default follows the OS.

Both the guided flow and Utilities stay fully usable after any toggle.

---

## 1 · RESIZE step (resolution, downscale-only)

- **Model:** `M` entry `id:'resize'` (~line 2646). Order: codec → quality → **resize**
  → saving → encoder → dest → ignore.
- **Options:** Leave alone (default/hot) · 4K·2160p · 1440p · 1080p · 720p · Custom
  max long-edge. Custom carries `preset:'custom'`; the px value lives in
  `ADV.resizeCustom` (added to `ADV_DEFAULTS`, ~line 3189).
- **Inline extra:** `#resize-extra` (stage HTML ~line 1353), rendered by
  `renderResizeExtra()` (~line 3151), styled with the `.dest-extra` / `.de-*` idiom
  plus new `.de-num` box. Never upscales — copy says so; a smaller source passes
  through untouched.
- **Glyph set:** `GLYPH.resize` (~line 2745) — an outer source frame with a nested
  "target" frame that shrinks per rung; Custom adds corner crop marks. Same milled
  line idiom as the container glyphs.
- **Corpse texture:** `TEX.resize` (~line 2740) — 6 recipes, field tightening as the
  cap drops.

## 2 · IGNORE step (ignore rules, surfaced in the flow)

- **Placement decision:** placed **last, after DESTINATION** (not after codec). It is a
  *scoping* gate ("which files"), not an encode decision, so it reads best as the final
  "…and skip these" before Start — matching the copy-deck framing ("files the scan
  pretends it never saw"). Putting it mid-flow would interrupt the "how should I
  encode" narrative.
- **Model:** `M` entry `id:'ignore'` (~line 2706). Options: Skip nothing (default) ·
  Skip tiny files (<50 MB) · Skip samples & trailers (by name) · Custom…
- **Shared state with Advanced:** the presets and the Custom fields read/write the
  **same** `ADV.ignUnderMb / ignOverMb / ignExts / ignNames` that the Advanced →
  Ignore files section edits. `armIgnorePreset()` (~line 3233) writes those keys on
  commit; `renderIgnoreExtra()` (~line 3176) surfaces the identical four controls
  inline and calls `advToForm()` so an open Advanced panel updates live. One state,
  two doors — edit either place, both agree. Advanced ignore section is unchanged and
  stays.
- **Glyph set:** `GLYPH.ignore` (~line 2771) — a filter funnel; each preset adds a
  different strike (small square / diagonal / tuning marks). `TEX.ignore` (~line 2779).
- **Comb at 7 teeth:** each `.tooth` is `flex:1`; at 7 across the full 1320px unit
  each is ~188px, so no font/padding change was needed — verified by rendering.

## 3 · Utilities mode

- **Toggle:** brow `#mode-seg` (HTML ~line 1371, CSS `.mode-seg/.mode-b` ~line 505),
  a quiet milled segment styled as picking a tool, not flipping a setting.
  `setMode()` (~line 5656) toggles `.utils` on `#unit`; CSS hides the guided flow
  (comb + main + deck) and shows `#utils-main`.
- **View:** `#utils-main` (HTML ~line 1468, CSS ~line 768). Landing = two tool cards
  (`.u-tool`) → results = scanned file list (`.u-row`) with per-file actions
  (`.u-act`) → footer Run.
- **Tools (mock):**
  - **Faststart remux** — scans, flags files whose moov atom is at the end, offers a
    container choice (Keep / To MP4 / To MKV) and a per-file **Remux**. Files needing
    it are pre-armed.
  - **File-health scan** — flags chapters past duration, NAL-unit errors (exit 69),
    broken containers; each flagged file offers its specific fix (**Remux** or
    **Re-encode**) **or Delete**.
- **Shared modals:** running calls the existing `pgStart/pgFile/pgDone1/pgFinish` and
  opens `#progress-sheet`; on finish it builds a report-shaped `RUN` via
  `uModelReport()` and opens `#report-sheet`, reusing `drawReport()` untouched. No new
  progress/report chrome. Mock data fabricated in `uMockFaststart` / `uMockHealth`
  (~line 5490).

## 4 · Dark colour scheme (auto + force)

- **Tokenised palette:** all themeable colours are CSS custom properties in `:root`
  (~line 25); a dark override lives in `:root[data-theme="dark"]` (~line 65). Light
  token values are the **exact original hex**, so light is pixel-identical (verified:
  token values compared against originals, and the light flow rendered against the
  known look — no regression). The milled bevel is two tokens (`--hl` light rim /
  `--sh` dark rim) that **invert** in dark, so engraved edges still read.
- **Dark palette:** warm graphite/anthracite in the same hue family as the paper (not
  a naive invert); accent orange retained, on-dark uses lifted for contrast.
- **JS-painted surfaces:** the corpse tiling and the option glyphs are painted in JS,
  so they read the theme off the tokens. `THEME` + `readTheme()` (~line 2804) mirror
  `--field-ink / --field-bed / --field-accent` via `getComputedStyle`; `paintCorpse()`
  calls `readTheme()` first, so the tiling re-colours (in dark the same three tile
  opacities become faint PALE highlights on a dark plate instead of shadows). Glyph
  fills already use `var(--accent)`.
- **Behaviour:** default follows `prefers-color-scheme`; a stored override (localStorage
  key `vtc-theme`) pins Light or Dark and persists. `applyTheme()` stamps `<html>` with
  `data-theme-pref` (drives the brow glyph) and `data-theme="dark"` (drives the token
  block). Live OS changes are followed while in Auto.
- **Control placement:** a subtle brow button (`#theme-btn`, ~line 1378) cycles
  Auto → Light → Dark on click (one-tap), **and** a full Auto/Light/Dark segmented
  control in **Advanced → Appearance** for a deliberate pick. Rationale: the brow is
  the fast affordance; Advanced is where a considered, sticky choice belongs.

---

## Mock-only — needs `webapp.py` wiring later

The mockup maps flow answers → `RunConfig` around `webapp.py:303`. New/changed inputs:

- **`resize` question** (new answer id). Map the chosen option to a downscale cap:
  Leave alone = none; 2160/1440/1080/720 = long-edge cap; Custom = `ADV.resizeCustom`
  (px). Engine must scale down only, preserving aspect, never upscaling.
- **`ignore` question** (new answer id). It only *arms the existing* `ADV.ign*` state,
  so the engine side may need nothing new beyond honouring those rules (already wired
  for Advanced). Confirm the presets ("tiny" = 50 MB, "names" = sample/trailer) match
  the intended defaults.
- **`ADV.resizeCustom`** — new persisted Advanced field; add to whatever round-trips
  ADV to disk.
- **Utilities** — entirely mock. Real wiring needs bridge calls to (a) scan for
  faststart candidates + remux (stream copy, optional container change) and (b)
  file-health probe + per-file remux/re-encode/delete. The view already speaks the
  shared progress/report protocol, so the bridge only needs to drive
  `pgStart/pgFile/pgDone1/pgFinish` and populate a report `RUN`.
- **Theme** — persisted to localStorage in the mock; the real app should round-trip the
  preference through ADV / its settings store instead so it survives with the rest.

## Open questions for the user

1. **RESIZE cap semantics** — "long edge" cap (works for portrait too) vs a height-only
   cap. I implemented long-edge; confirm that is what you want, and whether Custom
   should also allow a width×height rather than a single number.
2. **IGNORE placement** — I put it last (after Destination). If you'd rather it sit
   right after Codec (so the estimate reflects the filter earlier), it's a one-line
   move in `M`. Note the "tiny = 50 MB" and "samples/trailers" defaults — happy to
   change the numbers/words.
3. **Utilities scope** — the two v1 tools and their actions match the brief; confirm the
   file-health issue set (chapters-past-duration, NAL/exit-69, container faults) and
   whether re-encode-as-last-resort should be gated behind a confirm.
4. **Dark palette** — the graphite is deliberately warm. If you want it cooler/darker or
   the accent tuned differently on dark, it's a handful of token values in
   `:root[data-theme="dark"]`.

---

## Watch mode (5th addition)

A third top-bar mode — the segment is now **Compress · Utilities · Watch**
(`#mode-seg`, button ~line 1529). Watch is an ongoing background-service UI, a
sibling of Utilities: set-and-forget pipelines that standardise a folder for Plex
/ Jellyfin. Everything renders and works standalone on mock data.

### How to review (open the file in Chrome)

1. Top-right, click **Watch**. (In the standalone mock the gate overlay sits over
   every mode until a source folder is picked — same as Utilities; pick any folder
   first, or it's just the mock's start screen bleeding through. In the packaged
   app the mode switch is used after a folder is chosen.)
2. The **watcher list** shows one seeded pipeline ("Plex ingest"): a status line
   (● watching/paused · N done · M queued), **Start/Pause · Edit · Remove**, and a
   three-cell flow — **watch folder → folder-action chip → output folder** with a
   **keep/delete source** note. **+ add watcher** below.
3. **Start** a watcher → the status goes live (green pulsing lamp) and an activity
   list fills (queued → processing → done, on a mock timer). **Click a processing
   row** → the shared **`#progress-sheet`** runs a short mock encode; **click a done
   row** → the shared **`#report-sheet`** opens with the per-file detail. No new
   progress/report chrome.
4. **Edit / add** opens the watcher editor: name, watch folder, **folder action**
   (a saved profile, chosen from a select), **Capture the walkthrough…**, the
   **Optional step 2** faststart + health toggles, output folder, keep/delete.
5. **Capture the walkthrough…** → names the *current* Compress answers as a reusable
   folder action. The recap card shows all seven questions, making "a folder action
   IS the walkthrough, saved" literal.
6. Toggle the brow **theme** — Watch is native in both light and dark (all tokens).

### The folder-action model

A **folder action = the guided walkthrough, saved.** It stores the same thing the
compression flow collects: the seven answer INDICES (`codec, quality, resize,
saving, encoder, dest, ignore`) as `{ans:{…}}`, plus the two optional step-2
toggles (`faststart`, `health`). It is rendered as a **recap** using the confirm
sheet's own `dt/dd` idiom (`wActionSummaryRows` → `wRecapHTML`, ~line 6010), so it
visibly reads as the flow saved. `wActionOneLine` (~line 6023) makes the card
chip ("H.265 · Excellent · ≤1080p · replace"). A watcher references an action by
id, so several watchers can share one action.

### What was added, and where

- **Mode button** — `#mode-seg` gains a third `.mode-b[data-mode="watch"]` (~1529).
  `setMode()` (~6308) toggles `.watch` on `#unit`; CSS hides the guided flow (comb
  + main + deck) and Utilities, and shows `#watch-main`.
- **Status tokens** — `--live / --live-soft / --paused` added to `:root` (~60) and
  the dark block (~89). A running watcher reads green (not the accent, which means
  "armed choice" everywhere else); paused reads amber. Only new colours added.
- **CSS** — the `── Watch mode ──` block (~854–1000): `.wmain`, the watcher card
  (`.w-card` + `.w-lamp` pulse), the three-cell `.w-flow`, `.w-activity`, the
  `.w-add` slot, the editor (`.w-ed*`, `.w-fa-recap`, `.w-step2` + `.w-tog`
  switches, `.w-keepseg`). All milled to match — `u-tool`/recap/`mode-seg` idioms,
  theme tokens throughout, 2px radius.
- **HTML — main view** — `#watch-main` (~1681): the lead + a `.w-pipe` line
  ("Drop into watch folder → folder action standardises it → output folder"), the
  `#w-list` and `#w-add` button.
- **HTML — sheets** — `#watch-sheet` (the editor, ~2553) and `#fa-name-sheet` (the
  capture-and-name step, ~2622), placed beside the existing `#progress-sheet` /
  `#report-sheet` and using the same `.sheet` chrome + `openSheet`/`shutSheet`.
- **JS** — the `═══ Watch mode ═══` module (~5964–6306): state `W`, localStorage
  persistence (`W_FA_KEY='vtc-folder-actions'`, `W_WATCH_KEY='vtc-watchers'`), the
  list renderer + per-card controls, the mock activity feed (`wStartFeed`/`wTick`/
  `wStopFeed`), the handoff (`wOpenProgress` → `pgStart/pgFile/pgDone1/pgFinish`;
  `wOpenReport`/`wModelReport` → `drawReport` + `#report-sheet`), the editor
  (`wOpenEditor`/`wEdSave`), and capture (`wCaptureAction`/`wSaveCapture`). Seeds
  one folder action + one watcher on first run so the mode is legible standalone;
  deleting them sticks.

### Mock-only — needs real wiring later

The view already speaks the shared progress/report protocol, so the bridge work is
narrow. New state/ids for the backend to fill:

- **A real filesystem watcher.** `wStartFeed`/`wTick` fabricate the activity on a
  timer; the real thing watches `w.watch`, and for each new file runs the folder
  action's saved walkthrough through the engine, then places the result in `w.out`
  and keeps/deletes the source per `w.keep`. Drive the same
  `pgStart/pgFile/pgDone1/pgFinish` per file and build a report `RUN` (as
  `wModelReport` does) — no UI change needed.
- **Running a saved folder action = replaying the flow.** An action stores
  `{ans:{codec,quality,resize,saving,encoder,dest,ignore}}` (option indices, the
  same shape as `answers`) plus `faststart`/`health`. Mapping those to a
  `RunConfig` is the *same* mapping the one-shot flow needs (see the RESIZE/IGNORE
  notes above) — a watcher just applies it per-file, unattended, plus the optional
  step-2 pass.
- **Persistence.** Folder actions and watchers are in localStorage in the mock; the
  real app should round-trip them through its settings store (alongside ADV) so
  they survive with the rest. Keys/ids: `W.actions[]` (`{id,name,ans,faststart,
  health}`), `W.watchers[]` (`{id,name,watch,out,faId,keep,on,done,queued,
  activity}`).
- **Folder pickers** — `wPickFolder()` returns a mock path; wire to the native
  dialog for `#w-ed-watch-pick` / `#w-ed-out-pick`.
- **Step 2 lives on the action, not the watcher** — the faststart/health toggles
  are stored on the folder action (`fa.faststart`/`fa.health`) so every watcher
  using that action inherits them. If you'd rather they be per-watcher, it's a
  small move (read/write `W.editing` instead of `fa` in `wOpenEditor`/`wEdSave`).

### Open questions for the user

1. **Step-2 placement** — I hung faststart + health on the *folder action* (so it
   travels with the profile). Alternative: per-watcher. Which do you want?
2. **Single vs multiple watchers** — I built the list ("+ add watcher") since
   "standardise files for Plex" reads as set-and-forget pipelines. Confirm that's
   right (vs one watcher for v1).
3. **Keep/delete default** — the seeded watcher deletes the source (an ingest
   pipeline usually does). Confirm delete-source is a sane default, or flip it to
   keep.
4. **Status colour** — a running watcher reads **green**, deliberately NOT the
   accent (which means "armed choice" everywhere). If you'd rather it use the
   accent for consistency, it's two token values.
5. **Activity depth** — the mock shows recent done + in-flight + a little queue.
   Real watchers could accrue long histories; say whether you want a capped recent
   list (as now) or a full scrollable log per watcher.

---

## Dark-theme fix pass (issues from the annotated review)

The dark theme was only half-tokenised, the palette read "dirty brown", the toolbar
was crowded, and a `[hidden]`/`display:flex` bug painted empty accent pills. This pass
fixes all six reported problems. **Light theme is unchanged** except for the toolbar,
which was redesigned in BOTH themes on request. Nothing is committed.

### 1 · Completed the dark tokenisation (root cause of "brown-on-beige")
Several surfaces used hardcoded light hex that ignored the theme. Converted to tokens:
- `.rail` `#e2dfd6` → `var(--rail-bg)` (the right rail stayed light in dark).
- `.pv-side` `#e5e2d9` → `var(--paper-2)` (the whole bottom-left preview/deck control
  panel: PREVIEW / SAMPLE FROM / POSITION / CODEC / COMPARE / ALL TIERS / REFRESH /
  ABOUT).
- `.pv-codec button` and `.pv-cmp select` `#efece3` → `var(--paper-hi)` (the CODEC and
  COMPARE controls in the deck).
- `.rates button` (bottom-right playback-speed buttons) `#eae7de`/`#f2efe6` → token
  gradients; the `.on` state was `var(--ink)` bg + `#f2efe6` ink (invisible in dark) →
  `var(--accent)` + white.
- `.btn-run:disabled` `#cfccc2`/`#8d8a80` (pale-on-pale START) and `.btn-commit:disabled`
  `#eceadf` ink → `var(--track)` bg + `var(--ink-3)` ink (legible in both themes).
- `.tooth .v` / `.tooth.done` answer inks `#45413a`/`#3a372f` → `var(--ink-2)`/`var(--ink)`
  so a settled tooth's answer flips light in dark.
- `.compat-opt.on` `#fff` (white-on-white in dark) → `var(--paper-hi)`.
- The boot **gate** used a light wash; added a `--gate-scrim` token (light wash in light,
  dark wash in dark).
Every button now has legible label contrast in both themes — no pale-on-pale.

### 2 · New neutral dark palette + a live three-way chooser
The warm brown dark block was replaced with **three neutral variants**, keyed by
`data-dark` on `<html>` and defaulting to **Charcoal**:
- **Charcoal** (default) — `:root[data-theme="dark"]` base block.
- **Near-black** — `:root[data-theme="dark"][data-dark="black"]`.
- **Slate** (cool blue-grey) — `:root[data-theme="dark"][data-dark="slate"]`.
All three keep the milled bevel (inverted `--hl`/`--sh` rims) and the accent orange, but
neutral — no brown. A **"Dark style: Charcoal · Near-black · Slate"** segmented control
lives in **Advanced → Appearance** under the Theme row (`#adv-dark`). It persists to
`localStorage['vtc-dark']` (JS: `darkVariant()` / `setDarkVariant()`), applies via the
`data-dark` attribute in `applyTheme()`, and reads secondary/dimmed while Light is active
(the choice only takes effect in dark). The JS-painted comb tiling and option glyphs
follow the active variant automatically because `readTheme()` reads `--field-*`, which
each variant overrides.
Comparison renders of the same Quality-with-rail screen are in `design-review/`:
`dark-charcoal.png`, `dark-nearblack.png`, `dark-slate.png`.
**Recommended default: Charcoal** (neutral, a touch of warmth without brown). Near-black
is the most severe; Slate the most distinctive. The user picks from the renders.

### 3 · Layout regressions
- **Empty accent pills / blank area.** `.dest-extra` (and the report's `.r-trash-status`)
  set `el.hidden=true` to hide, but their `display:flex` overrode the UA `[hidden]` rule,
  so an off-step inline-extra rendered as three empty accent-bordered pills under the
  option buttons (and one under "SUCCESS · N" in the report). Added
  `.dest-extra[hidden]{display:none}` and `.r-trash-status[hidden]{display:none}` (plus
  the same guard for `.u-land`/`.u-results`). The meters below now read correctly with no
  dead vertical space.
- **Rail text cut off + stub scrollbar.** `.well` had `overflow:auto`; a hair of
  horizontal overflow produced a ~1cm stub scrollbar. Changed to
  `overflow-y:auto; overflow-x:hidden` + `min-width:0; overflow-wrap:break-word` so text
  wraps cleanly.
- **Hidden "next section" CTA.** The START/CONFIRM buttons were the pale-on-pale disabled
  styles above; now legible in both themes (START stays greyed-but-visible until all 7
  steps are answered, which is correct).
- **"QUALITY 249" tag.** Not a bug — `applyTierTags()` sets the well-tag to
  `Quality <H.264-bpp×100>`; Insane's bpp ×100 = 249. Intentional (codec-independent
  quality number). Left as-is; flag if the wording should change.

### 4 · Toolbar redesign (refined, four functional zones)
The old brow crammed mode + theme + gear + source + lamp into one right cluster. Rebuilt
as a 3-column grid with four clearly-grouped zones and breathing room:
1. **Identity** (left) — the wordmark with a single compact capability caption beneath it
   (`Encoders … · Quality …`), instead of two stacked blocks.
2. **Mode** (centre) — the primary segment, given prominence.
3. **Source** (right) — the SOURCE pill + status lamp ("what it's pointed at").
4. **System** (far right) — theme + gear, set off by a hairline divider (`.brow-sys`) so
   settings no longer read as part of the source group.
Rationale: grouping by function + a divider replaces the run-on shelf; centring the mode
segment makes the primary navigation the visual anchor. Rendered and verified in both
themes (`design-review/toolbar-light.png`, `toolbar-dark.png`).

### 5 · Mode rename
`Compress → Main`, `Watch → Folder Actions`. The segment is now **Main · Utilities ·
Folder Actions**. Only the button *labels* changed; the `data-mode` values
(`compress`/`utils`/`watch`) and all ids are unchanged, so `setMode()` and every handler
keep working. **No test text needed updating** — the jsdom suites (`tests/ui/*.js`) and
the Python suite don't assert the mode labels; all 132 pytest tests and all four jsdom
harnesses pass.

### 6 · File-health scan run modes
The File-health tool now offers a **run-mode** chooser in its results header
(`uRenderHealthMode()`), replacing the always-on per-file fix buttons:
- **Scan** — report only, no changes. No per-file actions; problem files show as findings;
  the footer reads "N files have a fault · report only, nothing changed" and Run opens the
  report (`rTab='fail'`).
- **Scan & remux** (default) — every problem file is armed to remux.
- **Also re-encode if remux still errors — at the file's existing BPP** — a sub-option
  shown only under "Scan & remux". A file that a container remux can't fix (a decode/NAL
  fault, `fix==='reencode'`) is offered **Re-encode** instead of Remux only when this is
  ticked; otherwise everything gets Remux. The sub-option greys out (dashed, disabled
  checkbox) under Scan, so the parent/child relationship is visible, not just enforced.
State: `U.healthMode` (`'scan'|'remux'`), `U.healthReencode` (bool). The report format the
user likes is unchanged. Renders: `/tmp/health-dark*.png` (scan, remux, remux+re-encode).

### Verification
Every fix was reproduced in headless Chrome (CDP driver), fixed, then re-rendered and
inspected. The full Main flow was walked in dark (gate, comb with filled teeth, Quality,
Saving, Destination-with-archive-extra, Ignore-custom, rail, meters, deck, playback bar,
CTA) — no surface renders light/beige and every label is legible. Light theme spot-checked
(Quality, Destination, report) — unchanged apart from the requested toolbar.

---

## Review-pass 2 — six directed changes (2026-09-01)

All six applied non-destructively; LIGHT theme is pixel-identical except the
label renames and the toolbar (already redesigned last pass). Every change was
rendered in headless Chrome (CDP) in both themes where visual; comparison PNGs
are in `design-review/`. Tests: **132 pytest + 51 jsdom checks pass.** Nothing
committed.

### 1 · Dark styles — two variants, default Slate (Charcoal dropped)
The dark block now ships **two** neutral variants, keyed by `data-dark` on
`<html>`:
- **Slate (default)** — the base `:root[data-theme="dark"]` block. bg `#1a1f27` ·
  panel `#1a1f27` · raised `#222834` · rule `#333c48` · ink `#e9ecf1` · ink-2
  `#9aa3af` · accent `#e8541f`. Needs no `data-dark` attribute.
- **Near-black** — `:root[data-theme="dark"][data-dark="black"]`. bg `#0c0c0d`-family
  (`#141416`) · panel `#141416` · raised `#1c1c1f` · rule `#2b2b2f` · ink `#f0f0f2` ·
  ink-2 `#9a9a9e` · accent `#e8541f`.
Charcoal is gone. JS: `DARK_VARIANTS=['slate','black']`, `darkVariant()` default
`'slate'`; `applyTheme()` stamps `data-dark` only for near-black (slate is the
base). **Migration:** a stored `'charcoal'` from the earlier build falls back to
slate. The Advanced → Appearance "Dark style" control now reads **Slate ·
Near-black** (label copy updated). The JS-painted comb tiling + glyphs still read
`--field-*`, so they follow the variant. Renders: `dark-slate.png`,
`dark-nearblack.png`.

### 2 · Mode rename — "Main" → "Compression"
The mode segment now reads **Compression · Utilities · Folder Actions**. Only the
button *label* changed; `data-mode` ids (`compress`/`utils`/`watch`) are
unchanged, so `setMode()` and every handler keep working. **No test text needed
updating** — the jsdom suites (`tests/ui/*.js`) and the Python suite don't assert
any mode label; all 132 pytest + 51 jsdom checks pass unchanged. Comment/caption
references to the old "Main" label were also updated. Renders: `toolbar-light.png`,
`toolbar-dark.png`.

### 3 · RESIZE → "Frame Size", new copy, HEIGHT-based cap
The step is renamed **Frame Size** (comb tooth `short:'FRAME SIZE'`, title "What
display size should the library be?"). New house-voice sub copy ("Would you like
to standardise your library to a specific display size?…"). **Cap semantics
changed from long-edge to VERTICAL pixels (rows) — height — exactly like
1080p/720p describe, aspect ratio always preserved.** Presets stay 2160p · 1440p ·
1080p · 720p, now worded as "Cap the height at …". **Custom = a single number in
vertical pixels** ("Max height ___ p"), not width×height and not long-edge;
downscale-only, sources already at/under the cap pass through untouched. Option
blurbs (`t`) and `renderResizeExtra()` all speak vertical-resolution terms; the
custom value still lives in `ADV.resizeCustom`. Renders: `framesize-light.png`
(with a 900 p custom entry), `framesize-dark.png`.
**Mock→real note:** the engine mapping (see below) must now cap HEIGHT, keeping
aspect ratio, never upscaling — a `scale=-2:min(H,cap)` style, not a long-edge cap.

### 4 · IGNORE → a transparency READOUT, not a config screen
The Ignore step (still LAST, after Destination) no longer offers inline
presets/editing. It now **reports the live ignore rules** read straight from the
shared `ADV.ign*` state as line items ("Smaller than · under 50 MB", "Extensions ·
.avi, .wmv", "Name contains · sample, trailer"), or, when nothing is set, says so
plainly ("**Nothing is being ignored** — every file in the folder is
considered."). A single **"Change in settings…"** button jumps to Advanced →
Ignore files (new `openAdvSection('ignore')` helper — opens Advanced and activates
that section). The step is now a single-option acknowledgement ("These will be
skipped") that **auto-arms** (`M` entry carries `readout:true`; `render()`
auto-arms a readout step), so the readout + Confirm show immediately. The old
inline preset/custom UI is removed from the step; Advanced → Ignore files stays
the single source of truth (and its section is unchanged, so the jsdom Advanced
ignore tests still pass). The estimate still reads `ADV.ign*` as before.
Renders: `ignore-readout-rules-light.png`, `ignore-readout-none-dark.png`,
`ignore-change-in-settings.png` (the jump landing on Ignore files with the same
rules shown).
**Honest limit:** the *mock* estimate is a library-level model (`SRC.tb`/`SRC.files`)
and never per-file-subtracted ignored files even before this change — the ignore
rules drive the *real* scan. The readout says "the estimate already leaves them
out", which is true of the real engine; the mock number is unchanged. Same
mock-only caveat as the original Ignore note above.

### 5 · Utilities — both tools are scans; "Scan & fix" added; user in control by default
Both tools now share ONE model, defaulting to Scan so the user stays in control:
- **Scan (default)** — scan, present findings, let the user **select** which files
  to fix (the obvious candidates are pre-armed as a convenience, freely
  changeable), then apply **"Fix selected"**. We stop after the scan on purpose.
- **Scan & fix** — scan, then **auto-apply** the fix to every applicable file and
  go straight to the shared `#report-sheet`. Rows are locked (the armed action
  still reads accent, but disabled) since the selection is automatic.
Faststart's control is **Scan · Scan & fix** (its container Keep/MP4/MKV choice
rides alongside). File-health's is **Scan · Scan & remux** with the conditional
sub-option **"Also re-encode if remux still errors — at the file's existing BPP"**,
enabled only under the fix path (dashed/disabled under Scan). State collapsed to a
shared `U.mode` (`'scan'|'fix'`, default `'scan'`) + `U.reencode` (bool); the old
`U.healthMode`/`U.healthReencode` are gone. `uRenderMode()` replaces
`uRenderHealthMode()` and now serves both tools; `uArm()` reconciles per-row armed
actions with the mode; `uRun()`/`uModelReport()` handle both paths and an
all-clean "nothing to do" report. Reuses the shared `#progress-sheet` +
`#report-sheet` untouched — the report layout the user liked is unchanged.
Renders: `utilities-faststart-scan-light.png`,
`utilities-faststart-scanfix-dark.png`, `utilities-health-scan-light.png`,
`utilities-health-scanremux-reencode-dark.png`.

### 6 · Folder Actions — "watcher" + "folder action" collapsed into ONE concept
The separate "saved profile" vs "watcher" split is gone. **A Folder Action IS the
whole rule**: a named pipeline **watch folder → sequence → output folder**, with
keep/delete source and a watching/paused (on/off) status. The **sequence** is the
guided-walkthrough answers PLUS the optional **step-2** (faststart + health)
toggles, **embedded in the action** and shown as an editable recap (no separate
profile to reference). State collapsed from `W.actions[]` + `W.watchers[]` (linked
by `faId`) to a single `W.actions[]` where each item carries everything:
`{id,name,watch,out,keep,on,debug,ans,faststart,health,done,queued,activity}`.
One localStorage key (`vtc-folder-actions`); an old split model is migrated on
load; the separate `#fa-name-sheet` "name this profile" step is removed (capture
now snapshots the current Compression answers straight into the action's `ans`).
- **Multiple actions:** a list with **"+ add folder action"**, seeded with three
  believable ones on first run — **Movies / TV / Anime** (localStorage, deletable).
- **Keep/delete source:** default **delete**, changeable per action.
- **Status colour:** running = **green** (`--live`), deliberately NOT the accent
  (which means "armed choice" everywhere else). Kept green.
- **Activity:** a **capped recent** list per card (in-flight + recent done + a
  little queue), never a full history. A new per-action setting **"Write a full
  debug log to file"** (off by default) is for when a full external log is wanted;
  a card shows a small `LOG` tag when it's on. Clicking an activity row still hands
  off to the shared `#progress-sheet` (in-flight) / `#report-sheet` (done).
- **Framing:** the copy reads as a persistent **background service** that "runs in
  the background, even when the app is closed", with a clear watching/paused
  control, and states the mental model "everything dropped in the watch folder is
  standardised by VTC before it's ingested elsewhere (Plex/Jellyfin)."
Renders: `folder-actions-list-light.png`, `folder-actions-running-dark.png`
(green status + capped activity), `folder-actions-editor-light.png` +
`folder-actions-editor-bottom-dark.png` (embedded sequence recap + keep/delete +
debug-log), `folder-actions-report-handoff.png` (a done row → shared report).

**Mock vs real (LaunchAgent):** the real implementation of a running Folder Action
is a launchd **LaunchAgent** — one per action, or a single agent servicing all —
so it keeps working while the app is closed. That is backend; the UI here is
UI-only, with the activity feed fabricated on a timer and the list in
localStorage. The "Write a full debug log to file" toggle is likewise UI-only for
now: the real service would write that external log when on. The view already
speaks the shared progress/report protocol, so wiring is narrow: a real fs watcher
runs each action's saved sequence (`ans` + step-2) through the engine per file,
places the result in `out`, and keeps/deletes the source per `keep`, driving the
same `pgStart/pgFile/pgDone1/pgFinish` + report `RUN`.

### Tests
`python -m pytest -q` → **132 passed.** jsdom `tests/ui/drive.js` → **51 checks,
0 fails.** No test text required updating: the mode rename touched only labels
(ids stable), and the jsdom Ignore/Advanced tests exercise the Advanced → Ignore
files section (unchanged), not the flow's Ignore step.

---

## Review-pass 3 — four directed changes (2026-09-02)

Applied non-destructively; LIGHT theme unchanged except the label renames; every
mode/step still works; self-contained (inline only). Rendered in headless Chrome
(served over `http://127.0.0.1:8899` — this Chrome refuses `file://` in headless,
so the review PNGs are captured over a throwaway local HTTP server) in BOTH themes.
Tests: **132 pytest + 51 jsdom checks pass.** Nothing committed.

### 1 · IGNORE step — readout redesigned for legibility (was illegible + over-built)
The previous readout leaned on `--ink-3` (the faintest ink) for both the intro and
the rule labels, and carried a pointless mono "These will be skipped" acknowledgement
key plus a `Read live from…` accent tag. All of that is gone. The step is now a clean
**readout panel only** — `render()` gained a `readout` branch that, for a `readout:true`
step, suppresses the option **key buttons**, the **meter row**, and the **hint**, and
paints a neutral well note ("nothing to preview here"). The whole readout lives in
`#ignore-extra` (`renderIgnoreExtra`), rebuilt as:
- **Exact intro copy (verbatim):** *The following files will be ignored. These rules
  are set in "Settings → Ignore files" and are applied to every run. Changes must be
  made in Settings.* — set in the new `.ig-intro` at `--ink-2` (clear secondary),
  never `--ink-3`.
- **All five rules as a labelled list**, read live from the shared `ADV.ign*` state
  (`ignoreRuleRows()`): **Smaller than · Larger than · Filename contains · Extensions ·
  Already processed (Yes/No)**. Each row shows its current value, or a plain **"—"**
  when unset. Rule labels are `--ink-2`; the **values are bright at `--ink`, weight
  600** (`.ig-list/.ig-row/.ig-lab/.ig-val`) — the legibility fix. "Already processed"
  mirrors the `ADV.ledger` toggle.
- **When nothing is set:** one plain legible line — *Nothing is being ignored — every
  file in the folder is considered.* (`.ig-empty`, at `--ink`).
- **One button — "Change ignore settings"** — jumps to Settings → Ignore files via the
  existing `openAdvSection('ignore')` (relabelled from "Change in settings…").
- The step still **auto-arms** (`readout:true` → `armed=0`), so it's a pure readout.
- The comb tooth for IGNORE now shows a **live rule count** ("N active" / "None")
  instead of the placeholder option key.
New CSS block `── IGNORE readout ──` (`.ig-intro/.ig-list/.ig-row/.ig-lab/.ig-val/
.ig-empty/.ig-act`), all off the ink tokens so both themes are legible. Renders:
`ignore-light-rules.png`, `ignore-dark-rules.png` (all five fields showing values),
`ignore-light-none.png`, `ignore-dark-none.png` (nothing set).

### 2 · Continue button never CLAIMS an update that isn't happening
The label was `answers[q.id]!==undefined ? 'Update' : 'Confirm'`, which said "Update"
even when re-opening a settled step and changing nothing, and on the readout step.
Replaced with a single `commitLabel(q, armedIdx)` used by **every** step:
- **first answer** → **"Confirm"**;
- **re-opened, armed differs from saved** → **"Update"**;
- **re-opened, armed equals saved** → **"Keep"** (nothing to change);
- **readout step** (nothing to arm) → **"Continue"**.
Verified live in the app DOM: `{fresh:"Confirm", changed:"Update", unchanged:"Keep",
ignore:"Continue"}`. The Ignore readout shows **"Continue"** (see its PNGs).

### 3 · "Advanced" → "Settings" everywhere (visible copy only; ids stable)
- Gear `title`/`aria-label`: "Advanced settings" → **"Settings"**.
- Modal title `<h2 class="sheet-t">`: "Advanced settings" → **"Settings"**.
- All visible "Advanced → X" copy → "Settings → X": the Quality-tiers explainer and
  ref-note, the container-compat sheet, the archive/destination pointers (dest-extra
  and the `dest` option blurb), and the empty-folder hint ("Settings → Ignore rules").
  The new Ignore intro already says "Settings → Ignore files".
- **ids unchanged** (`#adv-sheet`, `#adv-rail`, `data-sec`, `#adv-ign-*`, `#adv-jobs`,
  etc.), so `openAdv`, `openAdvSection`, `advToForm`/`advPush` and the tests keep
  working. Remaining "Advanced" strings are code comments only (not user-visible).
- **Test text:** none required updating. `tests/test_advanced_labels.py`,
  `tests/test_tuning_and_ignore.py` and `tests/ui/drive.js` reference the word
  "Advanced" only in **docstrings/comments** and assert by **id** and by the count of
  `#adv-sheet h3` headings (`>= 7`, still true) — no assertion on the modal title,
  group label or section names. Both suites pass unchanged.

### 4 · Settings group "Library" → "Processing" (two sections)
The rail group `Library` is renamed **Processing** and holds its two sections:
**Ignore rules** (was rail label "Ignore files"; section `<h3>` also → "Ignore rules")
and **Parallel jobs** (was rail label / `<h3>` "Running"). The section `data-sec` ids
(`ignore`, `run`) are unchanged, so navigation, the `openAdvSection('ignore')` jump and
the `#adv-jobs` control are untouched. Renders: `settings-processing-light.png`,
`settings-processing-dark.png` (the "Change ignore settings" jump landing on
Settings → Processing → Ignore rules, in both themes).

### Review harness note
A tiny inert hook at the end of the script reads `location.hash`: with no `#__shot=…`
it does nothing and ships as-is; with `#__shot=ignore&theme=…&rules=1|0` or
`#__shot=settings&theme=…` it drives existing functions (`pickFolder`, `render`,
`openAdvSection`) into the reviewed state so headless Chrome can screenshot it. It
adds no product behaviour and touches no shipped path.

### Tests
`python -m pytest -q` → **132 passed.** jsdom `tests/ui/drive.js` → **51 checks, 0
fails.** No test text changed.

### Re-applied after data-loss (2026-09-02)
Round-3 was lost in the data-loss incident (recovered only to round-2) and has now
been **re-applied faithfully** onto the current `vtc/vtc_app_v3.html`, matching this
spec and the reference PNGs. All four changes are back:
1. IGNORE step is a legible readout — exact intro copy, five labelled rows
   (Smaller than · Larger than · Filename contains · Extensions · Already processed)
   read live from `ADV.ign*`/`ADV.ledger` with bright `--ink` weight-600 values, the
   one-line "nothing set" case, and a single **"Change ignore settings"** jump; the
   old faint `--ink-3` readout, the mono "These will be skipped" key, the meter row
   and the hint are gone (new `.ig-*` CSS; `render()` readout branch; `readoutWell()`).
2. `commitLabel(q,armed)` drives every step's button: Confirm / Update (only when the
   armed value truly differs) / Keep / Continue (readout).
3. "Advanced" → "Settings" in all visible copy (gear title/aria-label, modal title,
   every "Settings → X" pointer); ids (`#adv-*`, `data-sec`) unchanged.
4. Settings group "Library" → **Processing** with sections **Ignore rules** +
   **Parallel jobs** (rail labels and `<h3>`s; `data-sec` ids `ignore`/`run` stable).
**Verification:** `python -m pytest -q` → **132 passed**; jsdom `tests/ui/drive.js` →
51 checks, 0 fails. Rendered in headless Chrome (both themes) over a throwaway local
HTTP server via an inert `#__shot=…` harness; fresh PNGs in `design-review/`:
`ignore-{dark,light}-{rules,none}.png`, `settings-processing-{dark,light}.png`.
All edits were made with surgical verified string replacements — the HTML was never
rewritten wholesale (no Write on the shipped file).
