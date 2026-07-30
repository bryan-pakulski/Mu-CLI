.PHONY: apk install clean prebuild help

# --- Configuration -------------------------------------------------
ANDROID_DIR  = android
GRADLEW      = $(ANDROID_DIR)/gradlew
APK_DIR      = $(ANDROID_DIR)/app/build/outputs/apk/release
APK_NAME     = mucli-mobile-0.1.0.apk
DIST_DIR     = dist
JAVA_HOME   ?= $(shell dirname $$(dirname $$(readlink -f $$(which javac) 2>/dev/null) 2>/dev/null) 2>/dev/null)

# Auto-detect Android SDK if ANDROID_HOME not set
ANDROID_HOME ?= $(or $(shell test -d $$HOME/Android/Sdk && echo $$HOME/Android/Sdk 2>/dev/null),$(shell test -d /opt/android-sdk && echo /opt/android-sdk 2>/dev/null))

# --- Targets -------------------------------------------------------

help:
	@echo "mucli mobile — local APK build"
	@echo ""
	@echo "  make prebuild  — generate android/ native project (expo prebuild)"
	@echo "  make apk       — build release APK, copy to dist/mucli-mobile-0.1.0.apk"
	@echo "  make install   — install APK to connected device via adb"
	@echo "  make clean     — remove build outputs + dist/"
	@echo ""
	@echo "Prerequisites:"
	@echo "  - JDK 17 (JAVA_HOME=$$JAVA_HOME)"
	@echo "  - Android SDK (ANDROID_HOME=$(ANDROID_HOME))"
	@echo "  - Node.js + npm deps installed (npm install)"

# Generate android/ native project from Expo config
prebuild:
	npx expo prebuild --platform android --clean

# Build release APK via Gradle (no cloud, no EAS account)
apk: prebuild
	@echo "sdk.dir=$(ANDROID_HOME)" > $(ANDROID_DIR)/local.properties
	@echo "✓ Wrote $(ANDROID_DIR)/local.properties (sdk.dir=$(ANDROID_HOME))"
	node scripts/patch-manifest.js
	mkdir -p $(DIST_DIR)
	cd $(ANDROID_DIR) && NODE_ENV=production JAVA_HOME=$(JAVA_HOME) ANDROID_HOME=$(ANDROID_HOME) ./gradlew assembleRelease
	cp $(APK_DIR)/app-release.apk $(DIST_DIR)/$(APK_NAME)
	@echo ""
	@echo "✓ APK ready: $(DIST_DIR)/$(APK_NAME)"
	@echo "  Install: adb install $(DIST_DIR)/$(APK_NAME)"

# Install to connected device (USB debugging enabled)
install: apk
	adb install -r $(DIST_DIR)/$(APK_NAME)

# Clean build outputs
clean:
	$(GRADLEW) -p $(ANDROID_DIR) clean 2>/dev/null || true
	rm -rf $(DIST_DIR)
