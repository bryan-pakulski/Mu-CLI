#!/usr/bin/env node
/**
 * Post-prebuild patch: adds cleartext traffic + network security config
 * to AndroidManifest.xml. Run after `expo prebuild`.
 *
 * Why: Expo SDK 51 doesn't map `usesCleartextTraffic` from app.json to manifest.
 * Android 9+ blocks cleartext HTTP by default, causing "Network request failed"
 * when the app tries to fetch http://192.168.x.x:30311
 */

const fs = require('fs');
const path = require('path');

const manifestPath = path.join(__dirname, '..', 'android', 'app', 'src', 'main', 'AndroidManifest.xml');
const resDir = path.join(__dirname, '..', 'android', 'app', 'src', 'main', 'res', 'xml');
const configPath = path.join(resDir, 'network_security_config.xml');

if (!fs.existsSync(manifestPath)) {
  console.error('patch-manifest: AndroidManifest.xml not found at', manifestPath);
  process.exit(1);
}

let manifest = fs.readFileSync(manifestPath, 'utf8');

// Add usesCleartextTraffic + networkSecurityConfig to <application> tag
if (!manifest.includes('android:usesCleartextTraffic')) {
  manifest = manifest.replace(
    /<application\s+([^>]+)>/,
    (match, attrs) => {
      const patched = `${attrs} android:usesCleartextTraffic="true" android:networkSecurityConfig="@xml/network_security_config"`;
      return `<application ${patched}>`;
    }
  );
  console.log('patch-manifest: Added usesCleartextTraffic + networkSecurityConfig');
} else {
  console.log('patch-manifest: usesCleartextTraffic already present');
}

fs.writeFileSync(manifestPath, manifest);

// Create network_security_config.xml
fs.mkdirSync(resDir, { recursive: true });
fs.writeFileSync(configPath, `<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="true">
        <trust-anchors>
            <certificates src="system" />
        </trust-anchors>
    </base-config>
</network-security-config>`);
console.log('patch-manifest: Wrote', configPath);