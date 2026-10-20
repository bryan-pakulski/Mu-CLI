# Mu — the quiet layer

**Status:** Adopted · 2026-10-19
**Applies to:** the browser GUI first; §11 carries the same ideas to Android and the terminal.
**Builds on:** [Wash](wash_design_guide.md). Wash is the *material*: paper, ink, the seven
watercolour pigments, glass, type and motion tokens. Mu is the *composition*: how much is on
screen, where each fact lives, how a page is built and how you move through it. Where the two
disagree, Mu wins; §12 lists the Wash sections it replaces.
**Visual reference:** [`reference/mu_reference.html`](reference/mu_reference.html) (open it in a
browser; screenshots are in `reference/mu-*.png`).

---

## 0. The idea

*Mu* is the Greek **μ**, the sign for something very small, and the Japanese **無**, which means
*nothing* or *without*. Mucli should feel like both: small enough to hold in one hand, and with
nothing on screen that you didn't need.

Wash gave Mucli a beautiful material. Every screen then piled that material up: a glass card
inside a glass panel on a glass page, a chip to name the card, a pill to repeat the count, and a
tagline to explain the page. Mu takes things away until each screen reads in one breath.

Three words sum it up:

- **Quiet.** Paper, a little ink, and colour only where something wants you.
- **Near.** What you need next is one keystroke or one click away and in the place you're
  already looking. You never hunt for it in a second panel.
- **Clear.** Every fact is said once, in plain words, in one place.

### What we're removing

These are taken from the current GUI (2026-10-19, at 1440 × 900):

| Pattern | Where it shows up today |
|---|---|
| **The same fact, many times** | Work says "needs you" five times on one screen: the top-bar pill, the *Needs you 1* summary card, the *Attention / Needs you 1* heading, the *Needs you* chip inside the card, and the sidebar count. Chat shows the mode twice (the top-bar `default` pill and the composer's *Default*). |
| **The page named three or four times** | Breadcrumb *Environments*, an eyebrow chip *Environments*, a title *Container environments*, a section heading *Environments*, and a two-line tagline. |
| **Boxes in boxes** | On Trace, each panel holds a label chip, a heading and a boxed table. On Environments, each card holds a chip, a key/value list, an inner "waiting" box and a footer row. Orchestration places the *Kill switch* card next to a *Fleet health* card that holds one chip. |
| **A status bar that knows everything** | Work's top bar has seven pills (`1 agent`, `0 checks`, `1 needs you`, `docker up`, `live`, `all sessions`) plus six icon buttons. |
| **Numbers that don't help a decision** | `0 checks`, *0 In review*, *Done this week 0*, *6 Nodes / 6 Online* side by side, and a trace summary of 18 metrics before the one diagnosis that matters. |
| **Labels for the obvious** | Every environment card carries an *Environment* chip, every template card a *Template* chip, and every trace panel a *Summary*, *Diagnosis* or *Context* chip. |
| **Marketing on a working screen** | *What should we ship next?*, *Your fleet, one operator* and *Delegate engineering jobs, then step in only when one needs you.* These appear on every visit, not only the first. |
| **Two navigations at once** | Inside Work the sidebar holds the 5 main destinations, then 7 Work sub-pages, then 2 Insights links. That is 14 rows to read before the content. |

---

## 1. Principles

1. **Say it once.** Each fact has one home (§6.1). If it already shows, a second rendering
   is clutter, even in another style.
2. **Space is the structure.** Group with whitespace first, then a hairline. Never use a box
   to group things. One glass surface per region; nothing boxed inside it.
3. **Name things by what they are.** The page title is the noun. Rows are named by their
   content. A label that repeats what the eye already knows is deleted.
4. **Show the next thing.** Each screen opens on the one or two things you are most likely to
   do next. Everything else waits one step away in a sheet, a disclosure or the command palette.
5. **Colour means "look here".** Most of a screen is paper and ink. A pigment appears only on
   something that wants you, is running, or has failed. Success is quiet.
6. **Sentences beat widgets.** "6 nodes, all online" beats two stat tiles. "Paused after the
   canary failed on web-1" beats a pill, a chip and a key/value row.
7. **The human is in charge, visibly.** This one comes from Wash and is not negotiable.
   Approvals, plan mode, YOLO and destructive confirmations stay the most legible things on
   screen. Removing clutter must make them stand out more, never less.

---

## 2. Material: one leaf on watercolour

The page is a painting with **one sheet of frosted glass laid on it**. We call that sheet
the *leaf*.

```
 ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
 ░ μ                ╭────────────────────────────────────────────────────────╮ ░
 ░                  │                                                        │ ░
 ░ ○ Chat           │   Work                                     + New job   │ ░
 ░ ▢ Work      1    │   1 needs you · 1 running · $7.56 today                │ ░
 ░ ▤ Orchestr. 1    │                                                        │ ░
 ░ ◇ Environments   │   Now · Jobs · Tickets · History                       │ ░
 ░                  │                                                        │ ░
 ░                  │   ◆ SCRUM-215  Soft delete & modification       23h    │ ░
 ░                  │     Review done · ready to push          Review  →     │ ░
 ░                  │   ─────────────────────────────────────────────────    │ ░
 ░                  │   ● SCRUM-221  Rate-limit login               12m      │ ░
 ░                  │     Implementing · 3 of 5 steps                        │ ░
 ░ ⌁ Insights       │                                                        │ ░
 ░ ⚙ Settings       ╰────────────────────────────────────────────────────────╯ ░
 ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
   painted wash, full bleed       the leaf: the only glass on the page
```

### 2.1 Three materials, no more

| Material | What it is | Used for |
|---|---|---|
| **Wash** | The ambient watercolour, full bleed and fixed. It softens to about 85% paper under the rail. | The ground. The rail sits directly on it. |
| **Leaf** | One glass sheet, `radius-xl`, with a 16px margin to the window on the top, right and bottom. | Everything you read: the page. |
| **Float** | `glass-strong` at elevation 2–3. | Things that come and go above the leaf: composer, menus, popovers, the detail sheet, dialogs, toasts, the command palette. |

**Rules**

- **One leaf per page.** Inside the leaf, structure comes only from space, type and hairlines.
  No cards, no panels, no inset boxes.
- **Nesting stops at two.** A float can sit on the leaf. Nothing glass ever sits inside a
  float, and nothing glass sits inside the leaf except floats.
- **Allowed tinted fills:**
  - The **decision block** (§7.6). It is safety-critical and uses its semantic wash, with no
    border.
  - **Code and diffs**, on `sunken`.
  - **Selected and hovered rows**, on `glass-subtle`.
- **No pigment edges.** As in Wash, edges are light, never colour.
- **Fallbacks.** "Reduce decoration", reduced transparency and forced colours all work as
  in Wash: the wash becomes flat canvas and the leaf becomes solid `surface`. The layout
  doesn't change.

### 2.2 The wash, quieter

- The ambient art stays (Dawn for chat, Lagoon for Work and Insights, Meadow for Environments,
  Dusk variants in the dark theme). It stays **in the margins**: around the leaf and under
  the rail, where it frames the page without competing with it.
- No hero bands. The painting already shows around the leaf.
- Vivid spot washes appear only in empty states and the first-run flow, one per screen.

---

## 3. Layout

### 3.1 The frame

The frame has two parts: **the rail and the leaf**. There is no global top bar.

```
┌──────────┬──────────────────────────────────────────────────────────────────┐
│ rail     │ leaf                                                             │
│ 208px    │  ┌ page header ────────────────────────────────────────────────┐ │
│ (56px    │  │ Title                                         Primary action │ │
│ collapsed│  │ state sentence                                               │ │
│ )        │  │ Tab · Tab · Tab                                              │ │
│          │  └──────────────────────────────────────────────────────────────┘ │
│          │  content (column ≤ 880, or full leaf width for tables and graphs) │
└──────────┴──────────────────────────────────────────────────────────────────┘
```

**Where everything from the old top bar goes**

| It was | It moves to |
|---|---|
| Breadcrumb | The page title. A detail page gets one "← Runs" link above its title. |
| Mode, model, effort, context meter | The composer's foot (chat only), which is where they're changed. |
| *Approvals* pill | Pending approvals in this chat are inline in the conversation. Approvals in other sessions show as a count on the session row in the rail. |
| *Live* / *docker up* / *connected* | Shown only when broken: a single line at the top of the leaf, "Reconnecting…" or "Docker isn't running · Start". Health that's fine is silent. |
| `1 agent`, `0 checks`, `1 needs you` | The page's state sentence (§4.2). |
| Theme, notifications, fullscreen, shortcuts | Settings, and the command palette. |

### 3.2 The rail

The rail is the only navigation. It lists destinations, never the sub-pages of a destination.

```
μ
○ Chat
▢ Work            1
▤ Orchestration   1
◇ Environments

  (chat only:)
  Sessions                 +
  Harvest-House
  mucli               ●
  orch-tool-studio    ● 1

⌁ Insights
⚙ Settings
```

- **Top group:** Chat, Work, Orchestration, Environments.
- **Foot:** Insights and Settings.
  - **Insights** absorbs Trace, Job analysis and Model pricing; they become tabs on one page.
  - **Settings** holds global settings and the theme.
- **Count badges** sit on a destination when something there needs you. This is the
  **global** home for "needs you" (§6.1). Badges use attention ink on attention wash. A
  collapsed rail shows them as a small bubble on the icon, as it does today.
- **Chat sessions** list under the destinations, on Chat only:
  - One line each: name, plus a `●` dot while it's running.
  - A pending approval shows as a count.
  - No avatars or monograms. The name is the identity.
  - The session type (chat, workspace, container) shows on hover and in the session menu.
- **Sub-pages** (Work's Jobs, Tickets, History…) become **tabs in the page header** (§4.3).
  The rail never grows a second level.
- **Collapsing:** the rail collapses to 56px icons with the button at its foot, as today.

### 3.3 Width and rhythm

- **Columns:**
  - The leaf is used whole; empty space is whitespace between things, never a dead half
    of the screen (revised 2026-10-20 after review).
  - Reading column: 680px, for chat and prose only.
  - **List pages split** from 1200px: the list on the left, the **dock** on the right
    (`clamp(340px, 38%, 600px)`, a hairline between them, no box). The dock shows the
    selected row's details (§7.2); on arrival it shows the first row that needs you, or the
    first row.
  - **Settings split** from 1200px: each form section's title and one-line lede on the left
    (sticky), its fields on the right (`.w-formsection`).
  - **Detail pages** (a run, a job) use two columns from 1200px: the main content, and a
    sticky right column with state, decisive actions and facts.
  - Tables, the trace timeline and run grids use the full width.
- **Four spacing steps**, nothing in between:

  | Step | Use |
  |---|---|
  | 8 | Within one item (glyph to text, title to meta) |
  | 16 | Between items in a group, and the row padding |
  | 32 | Between groups |
  | 64 | Between the page header and the content, and between major sections |

- **One left edge.** Titles, tabs, row text and section headings all start on the same x.
  Glyphs hang into the gutter to the left of it.
- **Leaf padding:** 48px at the sides (32px below 1200px, 16px below 840px), and 40px at the
  top.

---

## 4. Page anatomy

Every page is built the same way, top to bottom.

### 4.1 Title

- One line with the noun: **Work**, **Orchestration**, **Environments**, **Insights**,
  **Settings**. Detail pages use the item's own name, e.g. **sys.security-patch@1 on 2 nodes**.
- `title-1` in plum.
- No eyebrow above it and no tagline below it.

### 4.2 State sentence

- One line under the title in `ink-2`: what is true right now, written for a person.
  - *1 needs you · 1 running · $7.56 today*
  - *6 nodes, all online · 10 tools · dispatching*
  - *2 running · 1 template*
- **Leave out zeros**, unless the zero is itself the news ("Nothing needs you").
- Parts that need you take attention ink. The rest is ink-2.
- The sentence is a summary, not navigation; clicking a part may filter the list below.

### 4.3 Tabs

- Text tabs under the state sentence, at most four, then *More ▾*. They replace the sidebar
  sub-navigation.
- The active tab is in `ink` with a 2px ink underline. The others are `ink-3`.
- No counts on tabs. Counts live in the rail and the state sentence.
- Changing tab or item fades: the old content fades out over `motion-micro`, the new one fades in
  over `motion-medium` (another node, run, tool, job or environment as well as another tab; the
  list beside an item stays put). Any `[role=tab]` whose `aria-selected` turns true fades its
  panel in (`aria-controls`, else the nearest `[role=tabpanel]`); hash-routed pages call
  `WashMotion.leave(els)` before and `WashMotion.enter(el)` after a route change. Reduced motion
  makes it instant.

### 4.4 Primary action

- At most **one** ink pill per screen, on the title line at the right: *New job*, *New run*,
  *New environment*.
- Secondary actions are plain text buttons beside it (*Set up a node*), or live in the `⋯`
  menu.
- Shortcuts appear in tooltips and the palette, never as keycaps inside buttons.

### 4.5 Content

- The page then shows **Now** (§5.2) if anything needs you, then the main list.

### 4.6 First run and empty states

These are the only places for a sentence of explanation, a vivid spot wash and a display
heading:

```
                         (spot wash)
              Run reviewed tools across your machines.
          Nothing runs without a plan you can read first.

                       [ Set up a node ]
```

Once there is content, the explanation is gone for good. It stays reachable from `?`.

---

## 5. Showing content

### 5.1 Rows, not cards

The row is the unit of every list: jobs, runs, nodes, environments, tools, sessions and
tickets all use it.

```
  ◆  SCRUM-215  Soft delete & modification                          23h
     Review done · ready to push                       Review   Push   ⋯
  ──────────────────────────────────────────────────────────────────────
  ●  SCRUM-221  Rate-limit login                                    12m
     Implementing · step 3 of 5 · $1.20
```

| Part | Look | Rule |
|---|---|---|
| Mark | glyph + colour (§5.4) in the gutter | Always present; it's the row's state |
| Title | `body`, weight 500, `ink` | The thing's own name; IDs only if people say them aloud (ticket keys) |
| Line 2 | `caption`, `ink-3` | One sentence: the state in words, then at most two facts that change what you'd do |
| Time | `caption`, `ink-3`, right | Relative ("23h"); absolute in the tooltip |
| Actions | text buttons, right | Hidden until hover or focus, except the one action that's the reason the row is in **Now** |

- **Separation:** a hairline between rows, or nothing (just 16px of space) in short lists.
- **No surface:** the row has no fill until hover (`glass-subtle`) or selection.
- **Height:** at most two lines. A row that needs more is a detail sheet.
- **Opening:** the whole row is the target and opens the detail sheet (§7.2). Actions stop
  propagation.

**When a grid is allowed:** only for things you choose between visually, such as environment
templates in the "New environment" flow. Grid tiles are borderless:

- art or a glyph;
- the name;
- one line of text.

### 5.2 Now

The one place on a page where "needs you" is spelled out. It appears only when something
waits and sits above the main list:

```
  Now
  ◆  Paused  sys.security-patch@1 on 2 nodes                         6m
     The canary failed on web-1.                     Continue  Roll back  ⋯
  ◆  Accept  new-box · 10.0.0.12 · role=web                          2m
     Enrolled with a valid token.                          Accept   ⋯
```

- **Heading:** `Now`, in `title-3`, with no count. The rows are the count.
- **Order:** the order you must act in. Safety decisions first, then failures, then
  reviews.
- **Actions:** each row shows its decisive action without hover.
- **Emptying:** a row slides out (200ms) when handled, and a toast offers Undo where the
  command allows it. When Now empties, the section collapses away. There is no "You're all
  caught up" box; the state sentence already says it.

### 5.3 Details

- **Facts** in a detail view are a borderless two-column list: label in `ink-3` at 120px,
  value in `ink`. Group with 32px gaps, not boxes.
- **Show what explains the state;** fold the rest into **Details ▸**. For example, an
  environment shows *Running · used by wavebathy*. Image, network and worker port sit under
  Details.
- **IDs** (`r_54038e2b92c9`, image digests, ports) appear only under Details, in mono, with a
  copy button on hover.
- **Mono** is for things you'd copy or type: IDs, paths, commands, code. Never for labels,
  numbers or prose.

### 5.4 Status: one mark

Status is a **glyph plus a word**, never a pill. Colour is reserved for the states that ask
for your eyes.

| Group | Mark | Word colour | States |
|---|---|---|---|
| Needs you | `◆` attention | attention ink | awaiting approval, paused, review, awaiting acceptance |
| Running | `●` running, breathing slowly | `ink-2` | running, preparing, dispatching |
| Failed | `✕` danger | danger ink | failed, offline, rejected |
| Done | `✓` `ink-3` | `ink-3` | succeeded, done, published, active |
| At rest | `○` `ink-3` | `ink-3` | draft, stopped, cancelled, queued |

- The words come from `vocabulary.json`, as today; this changes the rendering only.
- **Success is quiet.** A list of finished things is calm grey with ticks. Your eye goes
  straight to the one diamond.
- **Effects** (read-only, mutating, destructive) are words in the row's second line, not
  chips. *Destructive* is in danger ink. *Mutating* is plain `ink-2`.

### 5.5 Numbers

- Show a number only if it could change what you do next. Totals that never move ("10
  published tools") belong in the state sentence or nowhere.
- **No metric strips or stat tiles.** Use a sentence, or one number with its unit inline.
- On analysis pages (Insights), lead with the **finding**, then the evidence. For example,
  *Tokenizer drift peaked at 41% on iteration 20 — likely why the context estimate was low*
  comes first, and the full metric table sits under **All metrics ▸**.
- Money is always `$1.20` with two decimals, tabular. Tokens are rounded (*841k in · 5.6k
  out*).

### 5.6 Tables

- **When to use one:** only for seven or more items that you compare across three or more
  columns (run history, audit, trace iterations). Otherwise use rows.
- **Structure:**
  - No outer box.
  - The header is `caption` in `ink-3`, with a hairline under it.
  - A hairline between rows. No zebra stripes.
- **Columns:**
  - Sticky first column.
  - Numbers right-aligned and tabular.
  - Status uses the mark (§5.4).
  - At most six visible columns; the rest go in the row's sheet.

### 5.7 Copy

- **Tone:** plain, short and calm. Write "The canary failed on web-1", not "Run paused due to
  canary failure threshold exceeded".
- **Case:** sentence case everywhere. No eyebrows, no ALL CAPS, no trailing colons on labels.
- **Buttons are verbs:** *Approve and start*, *Continue*, *Roll back*, *Accept*.
- **No taglines, no welcome copy after first run, no exclamation marks.**
- **Errors** say what happened and what to do, in one line, at the place it happened.

---

## 6. Where facts live

### 6.1 One home each

| Fact | Its home | Never also in |
|---|---|---|
| Something needs you (global) | The count badge on the rail destination | Top bar, page title, tabs |
| What needs you (local) | **Now** on that page | Summary cards, a second heading, a chip inside the row |
| A thing's state | Its row mark (§5.4) | A second chip, the row's header strip |
| How many there are | The state sentence | Stat tiles, tab counts, section heading counts |
| Mode, model, effort, context | The composer foot | The top bar, the session header |
| Connection and Docker health | Silent when fine; the leaf's top line when broken | Permanent pills |
| What a page is for | The empty state and `?` | A tagline on every visit |
| Keyboard shortcuts | Tooltips, the palette, and `?` | Keycaps in buttons and row text |
| IDs and raw config | Details ▸ in the sheet | Rows, cards, headings |

### 6.2 The duplicate test

Before adding anything, ask: **is this fact already on the screen?** If yes, make the
existing rendering better instead. If two components look different, they must mean
different things (Wash principle 2). If they mean the same thing, one of them goes.

---

## 7. Components (the whole kit)

Mu needs fewer components than Wash has today. This is the complete list for the web; anything
else should be built from these.

### 7.1 Rail item

Glyph, label, optional count. The active item has a `glass-strong` fill (the one place a
pill-shaped highlight sits on the wash, so you always know where you are).

### 7.2 Detail sheet and dock

- From 1200px, on list pages, the details render **in the dock** beside the list: no overlay,
  no dimming, the list keeps its keys. The selected row is marked.
- Below 1200px (or for flows that need a float), the same details are a float from the right
  edge, 520px (full width below 840px), over the list. The list stays visible and scrollable
  behind it, dimmed by 4%.
- Layout: title, mark + state sentence, the decisive actions, then facts and *Details ▸*.
- Esc closes it. ↑/↓ move to the previous or next item without closing. Its state is in the
  URL (`?open=r_54038e2b92c9`), so it survives a reload and can be shared.
- **Heavy things are pages, not sheets:**
  - A run with a node grid.
  - A job review with a diff.
  - The runbook editor.

  These open full-leaf with a "← Runs" back link above the title.

### 7.3 Button

| Kind | Look | Count per screen |
|---|---|---|
| Primary | ink pill | 0–1 |
| Secondary | text in `ink`, weight 500, underline on hover | any |
| Quiet | text in `ink-3` | any |
| Danger | text in danger ink; ink pill in danger only inside a confirmation | in context |

There are no outlined pills. An icon button is allowed only with a tooltip, and only for
universal glyphs (close, more, copy).

### 7.4 Inputs

- Fields are a 1px `control-border` bottom line, or a `sunken` fill with `radius-sm`, not
  boxed cards.
- Labels sit above in `label` `ink-2`. Help text sits below in `caption` `ink-3`, only when
  needed.
- Forms are one column at the reading width, with the button at the end, left-aligned with
  the fields.

### 7.5 Composer (chat)

- The composer is the one float in chat, `radius-xl` and centred on the reading column.
- **Body:** the text field.
- **Foot** (one line in `ink-3`), in this order: `+` attach · mode · model · effort, then
  the context meter ring and send on the right.
- Each foot item is text with a caret, opening a popover. Not a bordered select.
- When idle, the foot is `ink-3`. It turns `ink-2` when the composer is focused.

### 7.6 Decision block (safety-critical)

- **Used for:** approvals in chat, typed confirmations, and destructive dialogs.
- **Look:** the block is the semantic wash (attention, or danger for destructive) with no
  border. It shows:
  - what will happen, in one sentence;
  - the blast radius;
  - the exact command, path or diff, on `sunken`;
  - the decision buttons.
- **The only coloured surface** inside the leaf, so it is never lost among other tinted
  things.
- **Typed confirmation** stays: the input sits in the block.

### 7.7 Menu, popover and command palette

- Floats with 8px item padding.
- The palette (⌘K, or Ctrl K) is the universal way in: every page, action, session, job,
  run and node, with recent items first. It is the reason the screen doesn't need to show
  every action.

### 7.8 Toast

- Bottom centre, one at a time, 4 seconds, and Undo when possible.
- Only for outcomes that aren't visible where you are (a job finished in another session).
  For outcomes you can see, the row updating is the feedback.

### 7.9 Progress

- **Running:** a breathing `●` plus words ("step 3 of 5").
- **Determinate progress:** a 2px hairline bar under the row text, without a track.
- **Never:** spinners in buttons for more than 300ms. Show the state in the row instead.

---

## 8. Colour and type in practice

### 8.1 Colour budget

Wash's tokens are unchanged. Mu limits how many show at once.

- A calm screen is **paper, ink, ink-2, ink-3**, plus the wash around the leaf.
- **Attention** colour appears only on rows in Now, rail counts and the decision block.
- **Danger** colour appears only on failures and destructive actions.
- **Running** colour appears only on the breathing dot.
- Category pigments (modes, layers, agents) appear only as **a dot beside their name**,
  never as a filled chip.
- **Ceiling:** three pigments visible on one screen, in addition to the wash art.

### 8.2 Type budget

Five sizes, two weights (400, 500; 600 only for `title-3`):

| Role | Token | Use |
|---|---|---|
| Title | `title-1`, plum | Page and detail titles |
| Heading | `title-3` | Section headings (*Now*, *Recent runs*) |
| Body | `body` / `body-lg` in chat | Rows, prose, forms |
| Small | `caption` | State sentence parts, row second lines, metadata, table headers |
| Mono | `mono` | IDs, paths, commands, code |

`display` is reserved for first-run and empty states. `eyebrow` and `title-2` are retired from
working screens. Hierarchy comes from size and ink level, not from boxes or chips.

---

## 9. Flow

### 9.1 Arrive

- **Chat:** reopens the last session with the composer focused. A new user gets the empty
  state with one sentence and the composer.
- **Every other destination:** opens on **Now** if anything waits, otherwise the main list.
- Deep links (`?open=…`, tabs in the URL) restore exactly what you were looking at.

### 9.2 Notice

- Something new needs you: the rail count increments with a single 200ms scale-up, with no
  repeated pulsing.
- The tab title gains `(1)`. Browser and phone notifications follow the user's setting, as
  they do today.
- In chat, an approval for this session appears inline at the bottom of the conversation and
  the composer locks with "Decide above to continue".

### 9.3 Decide

The rule is **at most two moves**: count → row action, or count → row → sheet action.

- Inside Now, the decisive action is on the row.
- Anything needing more context opens the sheet, where the decision buttons come first and the
  evidence follows.

### 9.4 Return

- After a decision, the row leaves Now and you stay where you were. Focus moves to the next
  row in Now, or to the list if Now is empty.
- No redirect, no "Success" page. If the decision started something you might want to watch
  (a run), the toast says "Run started · Open".

### 9.5 Create

- One primary action opens a **sheet** with sensible defaults already filled in. Advanced
  options sit behind *More options ▸*. The submit button names the outcome: *Start run on 2
  nodes*, not *Submit*.
- Validation is inline as you type, attached to the field. A plan or preview (blast radius,
  batches) renders live at the end of the sheet, so the decision and its consequences are in
  one view.

### 9.6 Keyboard

| Key | Does |
|---|---|
| ⌘K / Ctrl K | Command palette |
| `/` | Focus the composer (chat) or search the list |
| `j` `k` or ↑ ↓ | Move between rows |
| Enter | Open the row's sheet |
| Esc | Close the sheet, menu or dialog |
| `n` | The page's primary action |
| `?` | Shortcuts and a short explanation of the page |
| `g` then `c` / `w` / `o` / `e` | Go to Chat, Work, Orchestration, Environments |

### 9.7 Motion

Wash's durations still apply. Mu uses only these moves:

- **Arrive:** fade + 4px rise, 160ms, the page header first, then content 40ms later.
- **Sheet:** slides 24px from the right with a fade, 220ms. It closes faster (160ms).
- **Row leaving Now:** height collapse + fade, 200ms.
- **Hover reveal** of row actions: 120ms opacity.
- **Running dot:** 2.4s breathe, opacity .45 → 1.

Nothing bounces or loops except the running dot. Reduced motion turns all of it into
instant changes.

---

## 10. Pages

### 10.1 Chat

```
╭──────────────────────────────────────────────────────────────────────╮
│                                                                      │
│   orch-tool-studio                                                   │
│                                                                      │
│   You                                                                │
│   Why did the patch run fail on two nodes?                           │
│                                                                      │
│   v1 passes all 16 security packages to a single apt-get install …   │
│                                                                      │
│   ▸ Read 3 files · ran 2 commands                                    │
│                                                                      │
│   ╭ decision ────────────────────────────────────────────────────╮   │
│   │ Run sys.security-patch@2 in check mode on web-1?             │   │
│   │ Read-only · 1 node                                           │   │
│   │ Approve and run      Reject                                  │   │
│   ╰──────────────────────────────────────────────────────────────╯   │
│                                                                      │
│      ╭────────────────────────────────────────────────────────╮      │
│      │ Ask μcli anything…                                     │      │
│      │ +   Default ▾   claude-opus-5-5 ▾   Effort ▾       ◔  ↑ │      │
│      ╰────────────────────────────────────────────────────────╯      │
╰──────────────────────────────────────────────────────────────────────╯
```

- **Header:** the session name is the only header. Hovering it shows the type and workspace.
  Its `⋯` menu holds rename, threads, export and delete.
- **Messages:**
  - No bubbles for the assistant: prose on the leaf at the reading width.
  - Your messages are prefixed with a quiet *You* label in `ink-3`, not boxed.
- **Tool activity:**
  - Collapses into one line per assistant turn: *▸ Read 3 files · ran 2 commands*.
  - Expanding it lists each call as a row; a call's output opens in the sheet.
  - Failed calls show their mark on the summary line.
- **Threads** move from a sidebar block into the session's `⋯` menu and the palette.
- **The stage** (mode panels) becomes the detail sheet, opened from the composer's mode menu.
  It closes with Esc like every sheet.

### 10.2 Work

- **Header:** title *Work*, the state sentence *1 needs you · 1 running · $7.56 today*, and
  *New job* on the right.
- **Tabs:** *Now · Jobs · Tickets · History*. Activity, Settings and Setup move to the page
  `⋯` menu. Setup stays as a Now row until it's complete.
- **Lists:** Now, then *Running*, then *Recent*, all rows.
- **Removed:** the summary cards, the hero, the *Attention* eyebrow, the top-bar pills.
- **A job:** opens as a page (review, diff, receipts). Its header has the job's title, the
  state sentence and the decisive action.

### 10.3 Orchestration

- **Header:**
  - Title *Orchestration*.
  - State sentence *6 nodes, all online · 10 tools · 1 draft · dispatching*, with
    *dispatching* as a quiet link to the kill switch.
  - *New run* as the primary action; *Set up a node* as a text button.
- **Tabs:** *Now · Runs · Nodes · Catalog*, with Studio, Audit and Settings under *More ▾*.
- **Approvals** is no longer a separate destination. Approvals *are* Now.
- **The kill switch:**
  - When dispatching, it's a quiet item in the page `⋯` menu and the palette (*Pause all
    dispatch*), behind a confirmation decision block.
  - When paused, it becomes the top line of the leaf in danger ink: *Dispatch is paused.
    Nothing new runs. · Resume*.
- **Recent runs:** one row per run. *4 of 6 done · 2 failed* sits in line 2; the effect is a
  word there. Proposed-by appears only when it's the AI.
- **A run:** opens as a page with the grid; the decision block sits under the title while it
  waits for you.

### 10.4 Environments

- **Header:** title *Environments*, state sentence *2 running · 1 template*, and *New
  environment* on the right.
- **Rows:**
  - Line 1: the name.
  - Line 2: *Running · used by wavebathy · 2.1 GB · 12% CPU*. Live numbers only once
    sampled; never a "Waiting for a resource sample…" box.
  - Hover actions: *Terminal*, *Stop*, `⋯`.
- **Templates** are a second section, as rows.
- **An environment:** opens in the sheet. Image, network and worker go under Details.

### 10.5 Insights

- **One destination** with tabs *Runs · Jobs · Costs* (formerly Trace, Job analysis and Model
  pricing).
- **Lead with the finding** (§5.5): the diagnosis sentence first, a small timeline, then *All
  metrics ▸* and the artifact table.
- **The run picker** is the page title's caret: *wavebathy · 23 iterations ▾*.

### 10.6 Settings

- **One reading column** with sections separated by 64px and a `title-3` heading. No cards
  per setting group. A setting is a row: label and help on the left, the control on the
  right.
- **The theme** lives here and in the palette.

---

## 11. Android and terminal

The same principles apply in each platform's idiom.

- **Android:**
  - The tab bar is the rail, with counts on tabs, as today.
  - Each tab opens on Now, then rows. Sheets are bottom sheets.
  - The header is the screen title only, with no chips.
  - One primary action per screen, as a floating ink pill at the bottom.
- **Terminal:**
  - The launcher lists destinations with a single "Needs you: …" description.
  - The toolbar carries one attention segment.
  - Command output leads with the finding, then rows. No tables for fewer than seven items,
    and no repeated headers.

---

## 11b. Revision 2: an application, not documents (2026-10-20)

After review on real data: spreading a list across the width is not using space. Mu is
an application with side-by-side views, flat surfaces and one persistent frame.

### Persistent shell

- A browser opening any destination gets **one shell** (`mu/gui/shell.py`,
  `templates/shell.html`, `static/js/mu_shell.js`): the rail, the mode's watercolour and a
  flat leaf. Each destination is a **keep-alive view** created on first visit and never
  torn down. Switching is instant; scroll, input, open details and live streams survive.
  No full page transitions.
- The address bar, back/forward, deep links and the tab title follow the active view.
  Links to another destination switch views; `MuNav.go(href)` does the same for scripts.
- API clients and `?standalone=1` still get the standalone pages.

### Flat, with the wash as accent

- Surfaces are flat paper: solid fills, hairline edges, no blur, no shadow on planes.
  Floats (menus, sheets, dialogs) keep one soft elevation.
- The watercolour lives only around the leaf, as the **mode's accent**. Every destination
  is a mode with a pigment: Chat apricot, Work sky, Orchestration lilac, Environments
  meadow, Insights lagoon, Settings plum. The accent marks the active rail item and the
  active tab.
- Changing mode is a **slow fade** (1.4s): the watercolour cross-fades and the accent
  eases from one pigment to the next. Reduced motion makes it instant.

### Layout: the bar, panes and boards

- **The bar.** The page header is one line: title, state sentence, tabs, actions. It
  sticks; the rest of the height is content.
- **Panes** (`.w-panes`): list | main | aside, full height, each scrolling on its own,
  hairlines between, never boxes. The list is draggable and remembered per page. Below
  1200 the aside opens over the main pane on demand; below 840 one pane shows at a time.
  - *list*: compact rows to choose from (filters on top).
  - *main*: the chosen thing itself, fully, in place: not a preview with "Open →".
  - *aside*: context for the chosen thing: state, decisive actions, facts, live log,
    timeline.
- **Boards** (`.w-board`): when nothing is chosen, the main area is an overview of flat
  sections side by side (needs you, running, fleet health, spend), each a few rows or one
  big number plus a meter or sparkline, never a wall of cards.
- **Show numbers usefully:** inline meters (`.w-bar`) for disk, memory, budget, and
  progress; sparklines for trends; one big number only when it is the point of the section.

### Per destination

| Destination | list | main | aside |
|---|---|---|---|
| Chat | sessions and threads | conversation | workspace panel (context, files, mode panels) |
| Work | jobs: Now, Running, Recent | the job: review, diff, plan, activity | stages, facts, decisive actions, live agent log |
| Orchestration | runs / nodes / tools | run grid / node / tool | node execution log / executions / runs and tests |
| Environments | environments, templates | live resources (charts) and the terminal | sessions, configuration, details |
| Insights | runs / jobs / models | charts and tables | suspects, key metrics, the selected model |
| Settings | sections | the section's fields | — |

With nothing chosen, Work and Orchestration show their board in *main*.

## 12. Relationship to Wash

| Wash section | Under Mu |
|---|---|
| §1 Colour, §2.1 Typefaces, §3.4 Borders, §5 Motion durations, §7 Voice, §11 Accessibility, §12 Token naming | **Unchanged.** Mu uses these tokens as they are. |
| §2.2 Scale | `eyebrow` and `title-2` retired from working screens; `display` only for first run and empty states. |
| §3.3 Elevation and glass | Level 1 glass is used once per page, as the leaf. Glass cards, panels and tables are retired. |
| §6 Atmosphere | The ambient wash stays and frames the leaf. Hero bands are retired. Spot washes only in empty states. |
| §8.1 Web app shell | The top bar is retired (§3.1). The stage becomes the detail sheet. Sub-navigation moves into page tabs. |
| §9.2 Chip, pill, badge | Chips are retired for status and labels. Badges remain only as rail and tab-bar counts. |
| §9.3 Card and panel | Retired for lists and sections. Rows and the leaf replace them. |
| §9.9 Approval card | Becomes the decision block (§7.6): same content and safety rules, no border. |

**New tokens needed:**

- `leaf` (glass at 64%, just above Wash's 62% floor; `radius-xl`; 16px window margin).
- `space-rhythm` (8 / 16 / 32 / 64).
- `row-min-height` (48 comfortable, 36 compact).
- `sheet-width` (520).

Everything else already exists.

## 13. Review checklist

A screen is done when all of these hold:

- [ ] No fact appears twice (§6.1). The page is named once.
- [ ] No glass inside the leaf except floats. No box inside a box.
- [ ] At most one primary ink pill.
- [ ] No chips for status or labels; states use the mark.
- [ ] No zero-valued numbers, unless the zero is the news.
- [ ] At most three pigments visible, excluding the wash art.
- [ ] No tagline or explanation outside empty states and `?`.
- [ ] Everything on it can be reached from the keyboard, and the main action is one key away.
- [ ] Approvals and destructive confirmations are the most visible things when present.
- [ ] Passes Wash's contrast and accessibility checks in Paper and Dusk, with decoration on
      and off.
