# mucli-mobile-android

React Native (Expo) mobile client for [Mu-CLI](../../README.md). Connects to a running mucli GUI instance via HTTP/SSE API and exposes all GUI functionality in a mobile-first UX.

## Setup

### Prerequisites

- Node.js 18+
- npm or yarn
- Expo CLI (`npm i -g @expo/cli`)
- A running mucli instance with GUI server enabled

### Install

```bash
cd mobile/android
npm install
```

### Development

```bash
# Start Expo dev server
npx expo start

# Run on Android emulator/device
npx expo run:android
```

### Build APK (local, no cloud)

No Expo account needed. Produces a shareable `.apk` you can install on multiple devices.

**Prerequisites:** JDK 17 + Android SDK (Android Studio or commandlineetools).

```bash
cd mobile/android
npm install        # one-time
make apk           # builds dist/mucli-mobile-0.6.2.apk
```

Install to a connected device:
```bash
make install       # builds + installs via adb (USB debugging enabled)
```

Or transfer `dist/mucli-mobile-0.6.2.apk` to any device, enable "Install unknown apps", tap to install.

### Build (EAS cloud)

```bash
# Install EAS CLI
npm i -g eas-cli

# Login to Expo
eas login

# Build APK
eas build --platform android --profile preview
```

## What's new in 0.6

Parity with the Mu GUI (`documentation/plans/mobile_parity.md`):

- **Sign in by pairing.** Scan the code from Settings → Devices on the computer; the token lives
  in the keystore and signs every request, stream and terminal. Unpaired phones are refused.
- **Six destinations** — Chat, Work, Orchestration, Environments, Insights, More — each with a
  page header (title, state sentence, tabs), rows instead of cards, and its own accent.
- **Chat:** inline approvals with breadcrumbs, the web's tool lists, a composer with the session
  controls and a mic (speech to text), one drawer for sessions and threads.
- **Work:** Now board, Notes, diff review mode, the check-artifact browser (logs, JUnit, XML,
  CSV, packet captures, archives, hex), server-driven job actions, "Open a shell".
- **Orchestration:** presence and busy nodes, maintenance, run decisions with typed
  confirmation, results tables, the catalog, Studio (tool drafts and runbooks) and the fleet chat.
- **Environments** with the terminal, **Insights** with costs, and themes: palettes and glass.
- Removed: the Workspace tab, the Audio and History screens, the settings sheets.

## Connection and sign-in

The phone signs in by **pairing** (see `documentation/security.md` §8).

1. On the computer, run the GUI so the phone can reach it:
   `MUCLI_GUI_ALLOW_REMOTE=1 mucli --gui --host 0.0.0.0`.
2. Open **Settings → Devices → Pair a device** in the browser (or run `mucli gui pair`). It shows
   a QR code and an 8-character code, valid for 10 minutes.
3. In the app, open **More → Settings → Devices** (or tap **Pair** on the banner) and scan the
   code, or type the address and the code.
4. The token is kept in the Android keystore (expo-secure-store), one per server, and signs every
   request, stream and terminal. Revoke it from the computer at any time
   (`mucli gui devices revoke NAME`); the app then asks to pair again.

## Architecture

```
mobile/android/
├── App.tsx                  # Fonts (useFonts + held splash), SafeAreaProvider, ThemeProvider, AppNavigator, PromptHost
├── app.json                 # Expo config (android.package=com.mucli.mobile); Paper/Dusk splash
├── Makefile                 # Local APK build (make apk / make install)
├── assets/fonts/            # Static Inter 400/500/600 + JetBrains Mono 400/600 TTFs (scripts/instance_fonts.py, from the variable sources; OFL licences)
├── assets/wash/             # Watercolour art: dawn/lagoon/meadow + spot washes, Paper and Dusk
├── src/
│   ├── api/                 # Typed HTTP/SSE client modules (1:1 with mucli GUI routers)
│   ├── ui/                  # The Wash component kit — every screen imports primitives from here
│   ├── components/          # Feature components (PromptHost, job views, sheets, drawers, CodeBlock)
│   ├── navigation/          # Root stack + six tabs; routes/<destination>.tsx per tab
│   ├── screens/             # Feature screens (Settings lives under More)
│   ├── store/connection.ts  # Zustand store: baseUrl, active session, provider/model, offline queue state
│   └── theme/
│       ├── wash.generated.ts  # GENERATED from mu/design/tokens.json + vocabulary.json — never edit
│       ├── tokens.ts          # Adapter: semantic colours, type, space, radius, motion, elevation
│       ├── status.ts          # Typed accessors over the shared status vocabulary
│       └── ThemeContext.tsx   # system | paper | dusk (persisted), Reduce decoration, StatusBar
```

### Navigation

Six tabs, the web GUI's destinations in the rail's order (`src/navigation/routes/<destination>.tsx`
owns each tab, its stack screens and its params). Switching tab cross-fades the destination's
watercolour and accent (1.4 s; instant with reduced motion).

| Tab | Contents |
|-----|----------|
| Chat | The conversation: tool lists, inline approvals, the composer (mode, model, effort, plan, auto-approve, context ring, mic). Sessions and threads in the left drawer; the workspace panel (mode panels, Context, Files, Session files, Skills) from ⋯ |
| Work | Now board, Jobs, Tickets, History; job page with Notes, Session, Plan, Diff (review mode), Review, Checks (artifact browser), Diagnostics, Cost & audit |
| Orchestration | Now, Runs, Nodes, Catalog; Studio (tool drafts, runbooks); Settings (read-only); fleet chat from Ask |
| Environments | Environments and templates with live meters; environment page with actions, charts, sessions and the terminal |
| Insights | Runs (traces), Jobs analysis, Costs (model pricing) |
| More | Settings (Appearance, Devices, Areas), Connection, Provider and model |

Deep links mirror the web's hash routes: `mucli://pair?code=…&url=…`, `mucli://work/job/<id>?tab=diff`,
`mucli://orchestration/run/<id>`, `…/node/<id>`, `…/tool/<id>/<version>`, `mucli://environments/env/<name>`,
`mucli://insights/costs`.

### Design system: Wash

The app follows the Wash design guide
(`documentation/design/wash_design_guide.md`, mobile notes marked "M:") and
shares one token and vocabulary source with the web GUI and the terminal.

- **Themes:** Wash and the palettes from `tokens.json` (theme cards under **More → Settings →
  Appearance**), each with a light (Paper) and a dark (Dusk) side: System / Light / Dark.
  **Glass** (Wash only) sets how much watercolour shows through surfaces, floor-clamped so text
  stays readable; palettes and **Reduce decoration** are flat. **Text size** (85–120%, shared
  with the web GUI's setting via `tokens.json` `density.text_scale`) scales text and line heights
  on top of the phone's own font size; smaller packs more on screen.
- **Colour:** components use semantic tokens only — `canvas`, `surface`,
  `sunken`, `raised`, `hairline`, `ink`/`ink2`/`ink3`, `plum`, and
  `accent` (lagoon), `running` (sky), `ok` (meadow), `attention` (apricot,
  "needs you"), `caution` (sun), `danger` (rose), `plan` (lilac), each with
  `wash` (fills), `pigment` (graphics, never text) and `ink` (text) steps.
  Modes, context layers L0–L5 and sub-agent series map to pigments.
- **Type:** Inter for UI, JetBrains Mono for code, on the Wash scale
  (display 48 … caption 12), sentence case everywhere.
- **Shape:** 4px spacing grid; radius 6/10/14/20/28/pill; pill buttons; soft
  hairline cards; sheets with radius-28 top corners.
- **Status vocabulary:** glyphs (`○ ● ◆ ✓ ✗ –`), semantics and canonical
  labels come from `wash.generated.ts`; no screen keeps its own table.
- **Interaction:** one `Pressable` (44dp minimum target, 0.98 press, haptics
  on primary and approval actions, role/state); reduce-motion honoured.
- To change a colour or label, edit `mu/design/tokens.json` or
  `vocabulary.json` and run `python3 scripts/design/build_tokens.py`.

See `ACCESSIBILITY_AUDIT.md` for what has and has not been verified.

### API Client

Each `src/api/*.ts` module maps 1:1 to a mucli GUI router (`mu/gui/routers/*.py`). The connection store (`src/store/connection.ts`) holds the configurable `baseUrl`, active session name, and active provider/model. All API calls go through `src/api/client.ts` which handles JSON parsing, error handling, and AbortSignal support.

## Testing

```bash
npx tsc --noEmit   # Type check
npx jest           # Unit tests
```