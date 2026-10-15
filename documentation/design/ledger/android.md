# Test migration ledger: Android (spec §5, M1–M3)

Every source-pinning assertion that the Android Wash work rewrote, per spec
§8.3. Format: `file:line — old → new — intent`. Line numbers refer to the
file after the change. No test was deleted without a replacement and none was
skipped.

## Dead-code removal (spec §2.2)

The unreferenced `ChatScreen.tsx`, `FeatureScreen`, `InspectorScreen`,
`SessionsScreen`, `SessionDetailScreen`, `TraceScreen`, `TraceDetailScreen`,
`TracesScreen`, `Header`, `SessionsDrawer`, `InspectorDrawer`,
`ArtifactsSheet`, `IconButton` and `Divider` were deleted. Tests that pinned the
dead legacy chat screen now pin the live one (`ChatScreenProduct.tsx`), which
is what users actually run.

- `tests/test_artifact_visualizations_subsection.py:20` — `CHAT_SCREEN = …/ChatScreen.tsx` → `…/ChatScreenProduct.tsx` — the chat jitter/visibility intents apply to the screen that ships.
- `tests/test_artifact_visualizations_subsection.py:139-145` — `"userScrollActiveRef.current || momentumScrollRef.current" in src` → a drag sets `followOutputRef.current = false` and `scrollToBottom` returns early when not following — the list never fights the user's finger; the live screen implements this with one follow flag instead of two refs.
- `tests/test_artifact_visualizations_subsection.py:154` — `"autoscrollToTopThreshold" in src` → removed; `maintainVisibleContentPosition` + `minIndexForVisible` still asserted — intent (no jump on insert) kept. The live screen anchors older-history loads through checkpoint anchors; an auto-scroll-to-top threshold would re-trigger the older-history load.
- `tests/test_inline_visualizations_v30.py:97` — reads `ChatScreen.tsx` → `ChatScreenProduct.tsx` — same intent, live screen.
- `tests/test_live_conversation_surfaces.py:98,102` — the duplicate `legacy` read of the deleted screen and its `not in legacy` assertion → removed; the identical assertion on `product` remains — intent unchanged.
- `tests/test_minimal_ui_actions.py:34-36` — `SessionsScreen.tsx` `switchSession(item.name)` / `Delete session ${item.name}` → `SwipeSessionsDrawer.tsx` `switchSession(session.name)` / `Delete ${session.name}` — session rows open on press and delete via a labelled icon, in the session list that ships.
- `tests/test_mobile_chat_stability.py:27,35,43` — reads `ChatScreen.tsx` → `ChatScreenProduct.tsx` — same intents, live screen.
- `tests/test_mobile_chat_stability.py:29` — `"messages.length === 0 ? styles.messageListEmpty : null"` → `"messages.length === 0 && styles.messageListEmpty"` — one composer mounted across empty/loading states (the live screen's spelling).
- `tests/test_mobile_command_completion.py:19` — `ChatScreen.tsx` → `ChatScreenProduct.tsx` — slash completion is wired into the live composer.
- `tests/test_mobile_container_terminal_artifacts_v28.py:26` — `ChatScreen.tsx` → `ChatScreenProduct.tsx` — artifacts strip sits above the live composer.
- `tests/test_mobile_safe_area_modals.py:47,49` — `SessionsDrawer.tsx`, `InspectorDrawer.tsx` removed from the list — the files no longer exist; the remaining drawers are still checked.
- `tests/test_mobile_streaming_perf.py:19` — `ChatScreen.tsx` → `ChatScreenProduct.tsx` — the live screen now has the throttled `scrollToBottom` (ported from the dead screen) so the throttle assertions hold unchanged.
- `tests/test_mobile_streaming_perf.py:77-80,89` — `"item.streaming ? ("` → `if (message.streaming)` precedes `<Markdown` in `renderAssistantBody` — streaming text renders as plain Text; Markdown only after finalize.
- `tests/test_mobile_syntax_highlighting.py:19` — `ChatScreen.tsx` → `ChatScreenProduct.tsx` — code highlighting is wired in the live chat.
- `tests/test_mobile_syntax_highlighting.py:180,195,206,227` — rule regex `\(node\)\s*=>\s*{` → `\(node(?::\s*\w+)?\)\s*=>\s*[({]` — same rules, typed arrow-expression spelling.

## M1 — token reskin

All colour keys moved from the planos legacy names to the Wash semantic shape
(`textDim`/`muted` → `ink3`, `textSoft` → `ink2`, `text` → `ink`, `border` →
`hairline`, `bg` → `canvas`, `glassStrong` → `raised`, `hover` → `sunken`,
`accent` → `accent.ink`, `accentSubtle` → `accent.wash`, `warn` →
`attention.ink`, `success` → `ok.ink`, `error` → `danger.ink`, `viz[n]` → the
matching semantic ink). Assertions that pinned a key name are renamed the same
way; the visual intent each one guards is unchanged.

- `mobile/android/__tests__/job-views.test.ts:88-94` (was 81-86) — dark/light text tokens ≥ 4.5:1 on bg/surface/raised → every Wash text token (ink, ink-2, ink-3, plum, each semantic ink) ≥ 4.5:1 on canvas/surface/sunken/raised in Paper and Dusk, each semantic ink ≥ 4.5:1 on its own wash, on-ink ≥ 4.5:1 on ink and accent, control-border ≥ 3:1 — contrast floor kept and generalised (spec §5.3 M1).
- `mobile/android/__tests__/job-views.test.ts:96-103` (was 88-93) — 24 pinned planos hex values → Paper and Dusk palettes equal `wash.generated.ts` (neutrals and every semantic's wash/pigment/ink) — no stale brand colours; one token source.
- `mobile/android/__tests__/job-views.test.ts:41` — test title "planos status vocabulary" → "shared status vocabulary" — wording only.
- `mobile/android/__tests__/plan-mode.test.tsx:111-117` — `colors.plan` ≠ `colors.warn` ≠ `colors.accent` (light/dark) → `plan.ink` ≠ `attention.ink` ≠ `accent.ink` and `plan.wash` ≠ `attention.wash` (paper/dusk) — plan mode never reads as attention or accent (lilac/apricot/lagoon).
- `tests/test_job_status_vocabulary_parity.py:59` — `test_web_and_mobile_tables_match_the_terminal` loops over web and mobile → web only (renamed `test_web_tables_match_the_terminal`) — Android no longer has a table to compare; see next entry.
- `tests/test_job_status_vocabulary_parity.py:68-73` — mobile `STATE_LABEL["provisioning"] == "preparing"` → the generated module embeds `provisioning` → `preparing` — canonical label everywhere.
- `tests/test_job_status_vocabulary_parity.py:76-88` (new) — `test_mobile_reads_the_generated_vocabulary_and_has_no_local_tables`: `status.ts` imports `wash.generated`, contains no `STATE_STATUS`/`STATE_LABEL`/glyph literals, and the generated module embeds every `job_state` row of `vocabulary.json` — replaces the literal-table parity check for Android (B8, spec §3.1).
- `tests/test_job_status_vocabulary_parity.py:93-100` — `NEEDS_YOU_STATES = new Set([...literal...]) == waiting` → `NEEDS_YOU_STATES = new Set(WAITING_STATES)`, `WAITING_STATES` filters the vocabulary by `status === 'waiting'`, and the vocabulary's waiting set equals the terminal's — needs-you states are the waiting states, now by construction.
- `tests/test_live_conversation_surfaces.py:138` — `borderLeftColor: colors.warn` → `borderLeftColor: colors.attention.ink` — the input-required banner keeps the attention rule.
- `tests/test_mobile_product_ui.py:30-39` — tokens.ts contains the planos hex values and `skyField`/`glassStrong` names → tokens.ts imports `wash.generated`, declares no hex literal, the generated file carries the Paper/Dusk canvases and lagoon ink, and the planos/old brand hexes appear in neither — mobile uses the same palette as web, from one source.
- `tests/test_mobile_product_ui.py:52` — `backgroundColor: colors.bg` → `colors.canvas` (atmosphere) — key rename.
- `tests/test_mobile_product_ui.py:67` — `card: colors.glassStrong` → `card: colors.raised` (navigation theme) — key rename.
- `tests/test_mobile_product_ui.py:79,83,86-87` — `colors.border` → `colors.hairline`, `toneColor || colors.accent,` → `toneColor || colors.accent.ink,`, `colors.bg` → `colors.canvas` (Card/Button/Input) — key rename.
- `tests/test_mobile_product_ui.py:102` — `borderBottomColor: colors.border` → `colors.hairline` (header) — key rename.
- `tests/test_mobile_product_ui.py:113,116` — `colors.glassStrong` → `colors.raised`; `colors.accentSubtle, borderColor: colors.accent` → `colors.accent.wash, borderColor: colors.accent.ink` (chat bubbles) — key rename.
- `tests/test_mobile_product_ui.py:137,140` — `colors.glassStrong` → `colors.raised` (sessions drawer, mode drawer) — key rename.
- `tests/test_mobile_product_ui.py:147-150` — `colors.viz[0]/[4]/[3]`, `colors.success : colors.warning` → `colors.accent.ink`/`running.ink`/`danger.ink`, `colors.ok.ink : colors.attention.ink` (trace) — key rename; the trace keeps semantic colours.
- `tests/test_mobile_syntax_highlighting.py:52-86` — `lightColors`/`darkColors` each declare a literal `syntax: {…}` block with 11 keys → `paperColors`/`duskColors` are built by `washColors()`, whose `syntaxFrom` builder defines all 11 keys from the generated Wash syntax slots (`keyword` → lilac, `string` → meadow, …) — both themes have a full syntax palette, now from tokens (G§9.12).

## M2 — one component kit

`src/ui/` replaces the legacy primitives (`Button`, `Card`, `Badge`, `Input`,
`Text`, `View`, `Skeleton`, `EmptyState`, `ErrorState`, `BottomSheet`,
`ModernBottomSheet`, `SafeAreaModal`, `AtmosphericBackground`) and
`planos.tsx`, all deleted. Every `TouchableOpacity` and raw `Pressable` moved
onto the kit `Pressable`. New coverage: `mobile/android/__tests__/wash-kit.test.tsx`.

- `mobile/android/__tests__/button.test.tsx:3` — `Button` from `src/components/Button` → `Button` from `src/ui` (same `title`/`onPress`/`disabled` assertions) — the kit button keeps the legacy `title` alias.
- `mobile/android/__tests__/bottomsheet.test.tsx:4`, `ui-polish.test.tsx:4` — `BottomSheet` from `src/components/BottomSheet` → `Sheet as BottomSheet` from `src/ui` — same render/close assertions on the one sheet implementation.
- `mobile/android/__tests__/{effort-settings,job-agents,job-environments,job-rules-form,job-sessions,job-setup}.test.tsx` (import line) — `ToastHost` from `src/components/planos` → from `src/ui` — import path only; toasts are asserted unchanged.
- `mobile/android/__tests__/threads-screen.test.tsx:33-34` — mock of `src/components/SafeAreaModal` → mock of the kit's `ModalFrame` in `src/ui/Sheet` — the create sheet still renders inline under jest.
- `tests/test_mobile_product_ui.py:10,12-15` — `AtmosphericBackground.tsx`, `components/Card|Button|Input|ModernBottomSheet.tsx` → `ui/WashBackground.tsx`, `ui/Card.tsx`, `ui/Button.tsx`, `ui/Form.tsx`, `ui/Sheet.tsx` — the shared surfaces now live in the kit.
- `tests/test_mobile_product_ui.py:50-57` — `<AtmosphericBackground>` in App, flat `colors.bg` atmosphere, "no gradient, glow or blur" → App renders `<AppCanvas>` on `colors.canvas`; `WashBackground` uses bundled local WebP art (`assets/wash/*.webp`), fades to canvas, and honours `reduceDecoration`; still no base64, remote URL, polygon or clip-path landscape — the atmosphere is local, calm and switchable (G§6).
- `tests/test_mobile_product_ui.py:81-96` — planos card (surface + border + radius 10), accent-fill primary button, canvas input, raised sheet with radius 10 → Wash card (surface + hairline + `radii.lg`, no shadow), ink-pill primary and surface/hairline-strong secondary on `radii.pill`, surface input with `controlBorder`, level-3 sheet with `radii.xl` top corners — shared surfaces stay quiet, now per G§9.1/§9.3/§9.5/§8.3.
- `tests/test_mobile_safe_area_modals.py:14-15` — `SafeAreaModal.tsx` owns the modal-local SafeAreaProvider → `ui/Sheet.tsx` (`ModalFrame`) owns it with the same `initialWindowMetrics`, `navigationBarTranslucent = false`, default edges — same intent, one owner.
- `tests/test_mobile_safe_area_modals.py:27,29` — raw `<Modal` allowed only in `SafeAreaModal.tsx` → only in `ui/Sheet.tsx`, matched as `<Modal[\s>]` so `<ModalFrame` is not a false positive — feature code never opens a raw native modal.
- `tests/test_mobile_safe_area_modals.py:41` — `"SafeAreaModal" in source` → `"ModalFrame" in source or "<Sheet" in source` — full-height flows go through the safe-area wrapper.
- `tests/test_mobile_safe_area_modals.py:46-55` — `ModernBottomSheet.tsx`/`BottomSheet.tsx` contain `SafeAreaModal` → `ui/Sheet.tsx` renders `<ModalFrame`; drawers and PromptHost use `ModalFrame` or `<Sheet` — sheets and drawers stay above the Android navigation bar.
- `tests/test_mobile_shell_screen.py:96` — `"Badge" in src` → `"<Chip" in src` — the shell connection status is a status chip (Badge retired, G§9.2).

## M3 — navigation and patterns

- `tests/test_job_review_surfaces.py:125-134` — header carries `accessibilityLabel="Open engineering work"` with `briefcase-outline` and a `label="Engineering work"` settings row → Work is a bottom tab (`<Tab.Screen name="Work"`, `tabBarAccessibilityLabel: 'Work'`), the header keeps a labelled `IconButton icon="briefcase" label="Open engineering work"`, and Containers moved to the Settings screen — Work stays one tap from chat (G§8.3).
- `tests/test_mobile_product_ui.py:100-113` — header `accessibilityLabel="Open Trace Analyzer"`, `analytics-outline`/`settings-outline` Ionicons, surface top bar, settings sheet groups as bordered panels → labelled Wash `IconButton`s (`activity`, `settings` → Settings screen), canvas top bar with a hairline bottom edge, and Settings as a real screen whose groups are sunken cards with the System/Paper/Dusk segmented control and Reduce decoration — same information hierarchy, Settings no longer a long sheet (G§8.3, G§9.3).
- `tests/test_mobile_syntax_highlighting.py:19-21,169-235` — the `fence`/`code_block`/`code_inline` rule assertions read `ChatScreenProduct.tsx` → read `ui/Markdown.tsx` (the one Markdown rules/styles module, spec §5.2); the chat imports `Markdown` from the kit — fenced code still goes through `CodeBlock` with the language from `sourceInfo`, inline code still uses `syntax.keyword`.
- `tests/test_mobile_streaming_perf.py:91-95` — `"from 'react-native-markdown-display'" in ChatScreenProduct` → the chat renders `<Markdown variant=…>` and `ui/Markdown.tsx` imports the renderer — Markdown is still only used after finalize.
- `tests/test_mobile_product_ui.py:114-136` — planos bubbles (`colors.raised` assistant with a squared top-left corner, accent-bordered user bubble, mono interim line, `settings-outline`) → Wash transcript: accent-wash user bubble with a 6px bottom-right corner and ≤80% width, bubble-less assistant prose, "Worked · N steps" interim row, tool calls as `ToolCard`s, labelled session-settings icon button, 44dp pill send — the chat stays flat, compact and single-pane (G§9.6–§9.8).
- `tests/test_artifact_visualizations_subsection.py:103,110` — `"VISUALIZATIONS"` / `"FILES"` headings → `>Visualizations<` / `>Files<` — the strip still splits visualizations from files; headings follow sentence case (G§2.2 "no ALL-CAPS labels").
