#!/usr/bin/env bash
set -euo pipefail

: "${CODESIGN_IDENTITY:?Set CODESIGN_IDENTITY to a Developer ID Application identity}"
: "${APPLE_ID:?Set APPLE_ID}"
: "${APPLE_TEAM_ID:?Set APPLE_TEAM_ID}"
: "${APPLE_APP_PASSWORD:?Set APPLE_APP_PASSWORD to an app-specific password}"

APP_PATH="${1:-dist/WatchdogPerf.app}"
ARCHIVE_PATH="${APP_PATH%.app}-notarization.zip"
FINAL_PATH="${APP_PATH%.app}-notarized-$(uname -m).zip"

codesign --force --deep --options runtime --timestamp \
  --sign "$CODESIGN_IDENTITY" "$APP_PATH"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ARCHIVE_PATH"
xcrun notarytool submit "$ARCHIVE_PATH" --wait \
  --apple-id "$APPLE_ID" \
  --team-id "$APPLE_TEAM_ID" \
  --password "$APPLE_APP_PASSWORD"
xcrun stapler staple "$APP_PATH"
rm -f "$ARCHIVE_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$FINAL_PATH"
echo "Created $FINAL_PATH"
