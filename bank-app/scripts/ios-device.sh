#!/usr/bin/env bash
# Build BVC bank, install it on the paired iPhone, and open Metro.
# The dev client reads --initialUrl, so the Metro address is not typed on the phone.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${EXPO_PORT:-8089}"
LAN_IP="$(ipconfig getifaddr en0 || ipconfig getifaddr en1 || true)"
if [[ -z "${LAN_IP}" ]]; then
  echo "No LAN address on en0 or en1." >&2
  exit 1
fi

UDID="$(
  xcrun devicectl list devices --json-output - 2>/dev/null | python3 -c '
import json, sys
data = json.load(sys.stdin)
for dev in data["result"]["devices"]:
    hw = dev.get("hardwareProperties") or {}
    conn = dev.get("connectionProperties") or {}
    if hw.get("platform") == "iOS" and hw.get("reality") == "physical" and conn.get("pairingState") == "paired":
        print(hw["udid"])
        raise SystemExit
sys.exit("No paired iPhone.")
'
)"

METRO_URL="http://${LAN_IP}:${PORT}"
if ! curl -sf "http://127.0.0.1:${PORT}/status" | grep -q running; then
  echo "Starting Metro at ${METRO_URL}"
  REACT_NATIVE_PACKAGER_HOSTNAME="${LAN_IP}" CI=1 \
    npx expo start --dev-client --host lan --port "${PORT}" >/tmp/bvc-metro.log 2>&1 &
  disown
  for _ in $(seq 1 40); do
    if curl -sf "http://127.0.0.1:${PORT}/status" | grep -q running; then
      break
    fi
    sleep 0.5
  done
  if ! curl -sf "http://127.0.0.1:${PORT}/status" | grep -q running; then
    echo "Metro did not start. See /tmp/bvc-metro.log" >&2
    exit 1
  fi
fi

echo "Building for ${UDID}"
xcodebuild \
  -workspace ios/SaoBank.xcworkspace \
  -scheme SaoBank \
  -configuration Debug \
  -destination "id=${UDID}" \
  -allowProvisioningUpdates \
  -derivedDataPath ios/build \
  build

APP="ios/build/Build/Products/Debug-iphoneos/SaoBank.app"
xcrun devicectl device install app --device "${UDID}" "${APP}"
xcrun devicectl device process launch \
  --device "${UDID}" \
  --terminate-existing \
  com.hainamtran.saobank \
  -- --initialUrl "${METRO_URL}"
echo "Opened ${METRO_URL}"
