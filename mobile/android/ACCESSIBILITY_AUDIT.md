# Accessibility audit — Android client 0.5.0 (Wash)

Date: 2026-10-15. Scope: `mobile/android/src` after the Wash migration
(spec §5, M1–M3). Checklist: `documentation/design/wash_design_guide.md` §11.

## How this was verified — and what was not

| Method | Done? |
|---|---|
| Source review of every screen and component, plus a script that lists `Pressable`s with no `accessibilityLabel` and no text child | Yes |
| Contrast computed from the generated tokens (`scripts/design/check_contrast.py`, `__tests__/wash-theme.test.tsx`, `__tests__/job-views.test.ts`) | Yes |
| Jest: roles, states and labels on the kit and on the approval flow (`wash-kit`, `approval-card`, `tool-approval-remember`, `wash-theme`) | Yes |
| TalkBack walkthrough on a device or emulator | **No.** No emulator or device was available in this environment. |
| Font scale 200% on a device | **No.** Only the code path (`maxFontSizeMultiplier={2}`) was checked. |
| Contrast on the real composited pixels over watercolour art | **No.** Only token-vs-token contrast was computed. |

Everything marked "No" must be done before calling the release accessible.
The spec's M3 acceptance (an emulator walkthrough of every tab in both themes
and with TalkBack) is still open.

## Contrast (computed from tokens)

| Theme | Lowest text on a surface | Lowest semantic ink on its own wash | `control-border` on surface/sunken | `on-ink` on `ink` |
|---|---|---|---|---|
| Paper | 5.02:1 (`ink-3` on `sunken`) | 5.21:1 (attention) | 3.05:1 | 17.1:1 |
| Dusk | 6.18:1 (`ink-3` on `raised`) | 6.26:1 (danger) | 3.49:1 | 15.9:1 |

All text tokens clear WCAG AA (4.5:1) on every surface token in both themes;
form-control boundaries clear 3:1. `pigment` steps are never used for text
(they colour dots, bars and rules only).

Not verified: text that sits near Wash art (Connect, Session start, Work).
The art fades to canvas (30% → 85% → 100% paper over the band) and content is
pushed below the band, but the composited pixels were not measured.

## Touch targets

- The kit `Pressable` (`src/ui/Pressable.tsx`) measures each control and adds
  hit slop until the target reaches 44×44dp. An explicit `hitSlop` can only
  grow the target, never shrink it (tested in `wash-kit.test.tsx`).
- Every former `TouchableOpacity` and raw `Pressable` now uses it, so the nine
  sub-44dp controls from the old audit (copy button 32×28, composer utility
  buttons 36×32, remove-chip ✕, interim disclosure 36dp, etc.) all reach 44dp.
  The chat copy button is now a labelled 44dp icon button.
- Buttons default to 44dp (`lg`); 36dp (`md`) buttons reach 44 through slop.
- Bottom tab items have a 44dp minimum.

## Names, roles and states

- Kit `Pressable` defaults to `accessibilityRole="button"`; disabled controls
  are 0.4 opacity **and** expose `accessibilityState.disabled`.
- Icon-only buttons (`IconButton`, `Button variant="icon"`) require a label.
  The scan found 24 unlabeled icon-only pressables in feature code (sheet close
  buttons, remove/download/delete row actions, drawer backdrops, Memory
  heat-map cells); all now have labels. The scan reports 0 remaining.
- Checkboxes expose `accessibilityState.checked` (the approval "Always allow
  this tool" checkbox included, fixing B6); switches carry their row label;
  tabs and segmented controls expose `selected`; disclosures (tool cards,
  "Worked · N steps", diff files) expose `expanded`; loading buttons expose
  `busy`.
- Status is never colour alone: every status glyph comes from the shared
  vocabulary with a visible or accessible label.
- Decorative art and icons are hidden from TalkBack
  (`importantForAccessibility="no-hide-descendants"`).

## Approvals (safety-critical)

- A new prompt is announced with `AccessibilityInfo.announceForAccessibility`
  ("Mucli needs your approval: …"); this is the Android equivalent of an
  assertive live region.
- When the approval card appears, screen-reader focus is moved to its heading
  (`setAccessibilityFocus`); Approve is never auto-focused.
- The approval sheet cannot be dismissed by swipe, scrim tap or the back
  button; the user answers, cancels, or explicitly presses "Minimize pending
  input".
- Not verified on TalkBack: the focus move and the announcement timing.

## Text scaling

- All kit text sets `maxFontSizeMultiplier={2}` (the OS may scale to 200%,
  never below the 12dp caption). Rows use `minHeight`, not fixed heights.
- Not verified on a device: older feature screens (container manager, new
  session wizard, memory heat map) still contain fixed-size boxes that may
  clip at 200%.

## Motion

- `useMotionPreference` follows the Android reduce-motion setting and pauses
  decorative motion in the background. The ink drop becomes a static ●, the
  skeleton stops breathing, sheets open without animation.
- Press feedback is an instant 0.98 scale, not an animation.

## Toasts and live updates

- Toasts are polite live regions; error toasts are assertive, are announced,
  and persist until dismissed.
- Streaming text is not announced per delta.

## Known gaps

1. TalkBack, 200% font scale and composited-contrast checks were not run on a
   device (see the first table).
2. Many feature screens still use Ionicons (outline variants) instead of the
   Wash icon set; they are labelled where interactive, but the icon language
   is mixed.
3. The bundled Inter and JetBrains Mono are variable fonts. Android may
   synthesise weights instead of using the variable `wght` axis; legibility
   of 500/600 weights was not checked on a device.
