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
