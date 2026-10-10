# DESIGN.md — jobhunt

A plain-text design system for AI agents building UI in this project.
Derived from the original author's portfolio stylesheet,
not invented — the dashboard should look like a designed site, not like Bootstrap.

Format follows the Stitch DESIGN.md specification.

---

## 1. Visual Theme & Atmosphere

Editorial, dark, and quiet. This is a working instrument, not a marketing page:
dense with information, calm in colour, and confident enough to leave space.
Think a well-set broadsheet printed on dark stock — serif body copy for reading,
a condensed sans for structure, and mono used as a label typeface rather than
for code.

Restraint is the theme. Colour is used to mean something, never to decorate.
A screen with one amber element on it is correct; a screen with six is broken.

## 2. Color Palette & Roles

```
--ink      #0D141B   page background
--ink-2    #121C25   raised surface: cards, panels
--ink-3    #18242F   highest surface: hover, active rows, nested panels
--rule     #22303C   all borders, dividers, hairlines
--paper    #E8EDF1   primary text
--muted    #8CA0B0   secondary text, labels, metadata
--signal   #E0A526   the single accent: links, the one thing that matters
--patina   #5FA895   secondary accent: confirmations, positive state
```

**Roles, strictly:**

- **`--signal` (amber) is scarce.** Links, and the highest-priority action. If
  two things on a screen are amber, one of them is wrong.
- **`--patina` (teal) is affirmative** — a good verdict, a live process, a
  success. Never a warning.
- **Never introduce red, green, or blue.** Severity is communicated by
  position, label and weight, not by a new hue. There is no `--danger` in this
  system on purpose: nothing in this application is an emergency.
- Surfaces step `ink → ink-2 → ink-3`. Never invent a fourth.

Dark only. There is no light theme and one must not be added.

## 3. Typography Rules

```
--ff-display  "Archivo", system-ui, sans-serif
--ff-body     "Newsreader", Georgia, serif
--ff-mono     "IBM Plex Mono", ui-monospace, monospace
```

- **Body copy is the serif**, at `1.0625rem / 1.65`. Reasoning, verdicts and
  prose are read, so they get a reading face.
- **Display is the condensed sans**, with **negative tracking** (`-.01em` to
  `-.025em`) at large sizes. Headings feel set, not stretched.
- **Mono is a label typeface.** The project's signature: tiny, uppercase, wide
  tracking.
  - section labels: `.6875rem`, `letter-spacing: .1em`, uppercase
  - micro labels: `.625rem`, `.1em`, uppercase
  - hairline labels: `.5625rem`, `.18em`, uppercase
  Mono is also correct for ids, counts and machine values.
- Never use the serif for a label, and never use mono for a sentence.

## 4. Component Stylings

**Surface / card**
`background: var(--ink-2)`, `border: 1px solid var(--rule)`,
`border-radius: 2px`. Flat by default.

**Chip** — the workhorse. A small bordered token for state.
`border: 1px solid var(--rule)`, `border-radius: 3px`, mono micro-label,
`padding: .2rem .5rem`. Modifiers:
- `.chip--live` carries a `.chip__dot` in `--patina` for an active state
- `.chip--link` is interactive and takes `--signal` on hover

Chips carry action names, buckets, counts and locations. Prefer a chip to a
coloured badge; prefer a chip to an icon.

**Band** — a section header. A `.band__label` in the hairline mono style above
a rule, with the content beneath. This is how the page is divided.

**Shell** — `max-width: var(--shell)` (1180px), centred, horizontal padding
`var(--gut)` = `clamp(1.25rem, 4vw, 3rem)`.

**Links** use `--signal` with an underline that appears on hover
(`opacity` transition), and shift `translateY(1px)` on `:active`. Never remove
the affordance entirely.

**Tables and dense lists**: hairline `--rule` separators, no zebra striping, no
outer border. Row hover raises to `--ink-3`.

## 5. Layout Principles

- One `.shell` per page. Content does not run edge to edge.
- **Vertical rhythm over boxes.** Group with a `.band` label and a rule, not
  with nested cards. Nesting more than one surface deep is a smell.
- Dense is correct. This is an instrument for reading many rows quickly; do not
  pad it out to feel airy.
- **Scannable left edge.** The most identifying text — a company, a role title —
  starts at the same x-position in every row so the eye can run down it.
- Metadata is secondary and looks it: `--muted`, mono, small, to the right or
  beneath.

## 6. Depth & Elevation

Depth is carried by **surface colour and hairline borders**, not by shadow.
Shadow appears only on genuinely floating things — a lightbox, a popover —
and when it does it is deep and soft:

```
box-shadow: 0 24px 40px -24px rgba(0,0,0,.9);
box-shadow: 0 24px 60px -24px rgba(0,0,0,.85);
```

Radii stay tight: `2px` for surfaces, `3px` for chips. Nothing is pill-shaped
and nothing is round.

## 7. Do's and Don'ts

**Do**
- Use `--signal` once per screen, for the thing that matters most.
- Label with tiny uppercase mono. It is the project's voice.
- Let the serif carry reasoning and prose.
- Communicate priority by order and position first.
- Keep borders hairline and radii tight.

**Don't**
- Add a colour. The palette is eight values and it is complete.
- Add a light theme.
- Use red for bad or green for good — there is no severity palette.
- Use emoji or icon fonts as meaning. A mono label says it better.
- Round the corners, add gradients, or add shadow to a static card.
- Nest surfaces more than one level deep.
- Centre body text, or justify it.

## 8. Responsive Behavior

Designed for a desktop browser first — this is a working tool on a large
screen. It must remain usable narrow, but need not be phone-optimised.

- `--gut` already scales: `clamp(1.25rem, 4vw, 3rem)`.
- Below ~820px, multi-column row layouts stack; the identifying text stays
  first and the metadata moves beneath it.
- Never hide information at a breakpoint. Reflow it.
- Touch targets, where interactive, are at least 40px tall.

## 9. Agent Prompt Guide

When building UI in this project:

> Use `DESIGN.md`. Dark only. Background `--ink`, cards `--ink-2`, hover
> `--ink-3`, hairline `--rule` borders, `2px` radii. Body copy in the
> `--ff-body` serif; headings in `--ff-display` with negative tracking; every
> label in tiny uppercase `--ff-mono` with wide tracking. `--signal` amber at
> most once per screen for the single most important thing; `--patina` teal for
> affirmative state. No other colours, no red or green, no shadow on static
> cards, no rounded pills, no icons carrying meaning. Prefer a `.chip` for
> state and a `.band` label for section structure. Dense over airy.

**Implementation notes for this project specifically:**

- Bootstrap 5.3 with `data-bs-theme="dark"`. The tokens above are already
  mapped onto `--bs-*` variables in the portfolio CSS — reuse that mapping
  rather than overriding Bootstrap component by component.
- Class naming is BEM-ish: `.block__element`, `.block--modifier`. Utilities
  take a `u-` prefix (`.u-mono`, `.u-display`).
- No build step. No React. Server-rendered Jinja2 partials, HTMX for updates.
- Fonts load from the same source the portfolio uses; if unavailable, the
  stacks degrade to `system-ui`, `Georgia`, and `ui-monospace` respectively,
  and that is an acceptable outcome.
