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
make apk           # builds dist/mucli-mobile-0.1.0.apk
```

Install to a connected device:
```bash
make install       # builds + installs via adb (USB debugging enabled)
```

Or transfer `dist/mucli-mobile-0.1.0.apk` to any device, enable "Install unknown apps", tap to install.

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

1. Open the app
2. Go to **Data** tab → **Connection**
3. Enter your mucli instance URL (e.g. `http://192.168.1.100:8000`)
4. Tap **Test Connection** — status indicator turns green on success
5. baseUrl persists across app restarts via AsyncStorage

## Architecture

```
mobile/android/
├── App.tsx                 # Root: SafeAreaProvider + ThemeProvider + AppNavigator
├── app.json                 # Expo config (android.package=com.mucli.mobile)
├── Makefile                # Local APK build (make apk / make install)
├── eas.json                 # EAS build profiles (apk)
├── src/
│   ├── api/                 # Typed HTTP/SSE client modules (1:1 with mucli GUI routers)
│   │   ├── client.ts        # Fetch wrapper with baseUrl + AbortSignal + JSON error handling
│   │   ├── sse.ts           # SSE subscription via react-native-sse
│   │   └── *.ts             # Per-feature API modules
│   ├── components/           # Base UI components (Text, Card, Button, BottomSheet, etc.)
│   ├── navigation/
│   │   └── AppNavigator.tsx # 5 bottom tabs (Chat, Sessions, Tools, Run, Data) with stack nav
│   ├── screens/             # Feature screens (19 GUI feature areas)
│   ├── store/
│   │   └── connection.ts    # Zustand store: baseUrl + active session + provider/model
│   └── theme/
│       ├── tokens.ts        # Design tokens (spacing, type, radii, colors)
│       └── ThemeContext.tsx # Light/dark theme via useColorScheme
```

### Navigation

5 bottom tabs cover all 19 GUI feature areas:

| Tab | Screens |
|-----|---------|
| Chat | Chat, Modes, Prompts, System Prompts, Inspector, Teacher, Feature, Research, Security, Loop, Debug, History |
| Sessions | Sessions list, Session detail |
| Tools | Memory, Files, Skills, Audio |
| Run | Traces (run list + drill-down summary) |
| Data | Providers, Connection settings |

### Design System

- **One accent color** (indigo #6366F1), neutral gray ramp
- **Spacing scale**: 4/8/12/16/24/32/48/64
- **Type scale**: 12/14/16/20/24/32
- **Touch targets**: ≥44px
- **Tabular nums** on numeric columns
- **States first-class**: empty, loading (Skeleton), error (ErrorState), disabled
- **No side panels** — mobile-first, FlatList/ScrollView only
- **Light/dark** via `useColorScheme`

### API Client

Each `src/api/*.ts` module maps 1:1 to a mucli GUI router (`mu/gui/routers/*.py`). The connection store (`src/store/connection.ts`) holds the configurable `baseUrl`, active session name, and active provider/model. All API calls go through `src/api/client.ts` which handles JSON parsing, error handling, and AbortSignal support.

## Testing

```bash
npx tsc --noEmit   # Type check
npx jest           # Unit tests
```