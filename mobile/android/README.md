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
make apk           # builds dist/mucli-mobile-0.5.0.apk
```

Install to a connected device:
```bash
make install       # builds + installs via adb (USB debugging enabled)
```

Or transfer `dist/mucli-mobile-0.5.0.apk` to any device, enable "Install unknown apps", tap to install.

### Build (EAS cloud)

```bash
# Install EAS CLI
npm i -g eas-cli

# Login to Expo
eas login

# Build APK
eas build --platform android --profile preview
```

## Connection

1. Open the app — without a server it shows the **Connect** screen
2. Tap **Connect** (or **More → Connection**)
3. Enter your mucli instance URL (e.g. `http://192.168.1.100:30311`)
4. Tap **Test Connection** — the status chip turns to Live on success
5. baseUrl persists across app restarts via AsyncStorage

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
│   ├── navigation/          # Root stack + bottom tabs (Chat, Work, Workspace, More)
│   ├── screens/             # Feature screens (Settings lives under More)
│   ├── store/connection.ts  # Zustand store: baseUrl, active session, provider/model, offline queue state
│   └── theme/
│       ├── wash.generated.ts  # GENERATED from mu/design/tokens.json + vocabulary.json — never edit
│       ├── tokens.ts          # Adapter: semantic colours, type, space, radius, motion, elevation
│       ├── status.ts          # Typed accessors over the shared status vocabulary
│       └── ThemeContext.tsx   # system | paper | dusk (persisted), Reduce decoration, StatusBar
```

### Navigation

A native stack wraps four bottom tabs. The edge-swipe drawers (sessions from
the left, modes from the right) remain as shortcuts.

| Tab | Contents |
|-----|----------|
| Chat | Conversation with tool cards, approvals, composer; header links to trace, work and settings |
| Work | Engineering jobs dashboard (Jobs, Activity, Tickets, History, Job settings pages) |
| Workspace | Workspace tools grouped by intent (memory, files, skills, modes, shell, …) |
| More | Settings, connection, provider, modes, threads, session files, trace, pending prompts |

### Design system: Wash

The app follows the Wash design guide
(`documentation/design/wash_design_guide.md`, mobile notes marked "M:") and
shares one token and vocabulary source with the web GUI and the terminal.

- **Themes:** Paper (light, `canvas #F8F6F1`) and Dusk (dark, `canvas #16131B`).
  The preference is System / Paper / Dusk under **More → Settings → Appearance**
  and persists across restarts. **Reduce decoration** replaces watercolour art
  with a flat wash.
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