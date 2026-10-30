#!/bin/sh
# Dừng đồng hồ, xoá dữ liệu demo sau 20/9, nạp lại RDB và persona, đưa đồng hồ về 09:00.
set -eu
BANK=${BANK_URL:-http://127.0.0.1:8092}
ENGINE=${ENGINE_URL:-http://127.0.0.1:8091}
DATABASE_URL=${DATABASE_URL:-postgresql://hainamtran@localhost:5432/fides_w1}
REDIS_URL=${REDIS_URL:-redis://localhost:6379/15}
REDIS_PORT=${REDIS_PORT:-6379}
REDIS_HOST=${REDIS_HOST:-127.0.0.1}
REDIS_MODE=${REDIS_MODE:-host}
API_KEY=${FIDES_API_KEY:-fides-demo-key}
DB=${REDIS_URL##*/}

curl -sf -X POST "$BANK/clock/pause" >/dev/null

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 <<'SQL'
ALTER TABLE decisions DISABLE TRIGGER USER;
ALTER TABLE responses DISABLE TRIGGER USER;
DELETE FROM responses
WHERE ts >= TIMESTAMPTZ '2026-09-20T00:00:00+07:00'
   OR decision_id IN (SELECT decision_id FROM decisions WHERE ts >= TIMESTAMPTZ '2026-09-20T00:00:00+07:00');
DELETE FROM decisions WHERE ts >= TIMESTAMPTZ '2026-09-20T00:00:00+07:00';
ALTER TABLE decisions ENABLE TRIGGER USER;
ALTER TABLE responses ENABLE TRIGGER USER;
ALTER TABLE events DISABLE TRIGGER USER;
DELETE FROM events WHERE ts >= TIMESTAMPTZ '2026-09-20T00:00:00+07:00';
ALTER TABLE events ENABLE TRIGGER USER;
CREATE TABLE IF NOT EXISTS bank.experiment_sessions (
    id bigserial PRIMARY KEY,
    participant_id text NOT NULL,
    condition text NOT NULL,
    session_index integer NOT NULL,
    task_type text NOT NULL,
    call jsonb NOT NULL,
    behaviour_features jsonb NOT NULL,
    raw_events jsonb,
    created_at timestamptz NOT NULL
);
TRUNCATE bank.entries, bank.transfers, bank.products, bank.guardian_requests,
         bank.guardians, bank.beneficiaries, bank.accounts, bank.idempotency, bank.call_notices,
         bank.experiment_sessions;
SQL

# DEBUG RELOAD bị tắt. Tắt Redis không ghi đè file, rồi bật lại để nạp đúng RDB.
# REDIS_MODE=docker chỉ khởi động lại service redis của compose, không đụng Redis trên máy.
if [ "$REDIS_MODE" = "docker" ]; then
  docker compose restart redis >/dev/null
else
  redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" SHUTDOWN NOSAVE >/dev/null 2>&1 || true
  REDIS_SERVER=${REDIS_SERVER:-redis-server}
  REDIS_CONF=${REDIS_CONF:-/opt/homebrew/etc/redis.conf}
  "$REDIS_SERVER" "$REDIS_CONF" --daemonize yes
fi
i=0
while true; do
  out=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" -n "$DB" DBSIZE 2>&1 || true)
  case "$out" in
    *ERR*|*LOADING*|*loading*)
      i=$((i + 1))
      if [ "$i" -gt 600 ]; then
        echo "redis không lên: $out" >&2
        exit 1
      fi
      sleep 0.5
      ;;
    *)
      case "$out" in
        ''|*[!0-9]*)
          i=$((i + 1))
          if [ "$i" -gt 600 ]; then
            echo "redis không lên: $out" >&2
            exit 1
          fi
          sleep 0.5
          ;;
        *)
          if [ "$out" -lt 1000000 ]; then
            i=$((i + 1))
            if [ "$i" -gt 600 ]; then
              echo "redis chưa nạp đủ snapshot: $out" >&2
              exit 1
            fi
            sleep 0.5
          else
            break
          fi
          ;;
      esac
      ;;
  esac
done
# Chỉ xoá kho episode và baseline thao tác. SCAN + DEL, không FLUSHDB.
# BEGIN purge_prefix
purge_prefix() {
  pattern=$1
  label=$2
  removed=0
  cursor=0
  while true; do
    out=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" -n "$DB" --raw SCAN "$cursor" MATCH "$pattern" COUNT 500)
    cursor=$(printf '%s\n' "$out" | head -n 1)
    keys=$(printf '%s\n' "$out" | tail -n +2)
    if [ -n "$keys" ]; then
      n=$(printf '%s\n' "$keys" | wc -l | tr -d ' ')
      printf '%s\n' "$keys" | xargs -n 100 redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" -n "$DB" DEL >/dev/null
      removed=$((removed + n))
    fi
    if [ "$cursor" = "0" ]; then
      break
    fi
  done
  echo "$label removed $removed"
}
# END purge_prefix
purge_prefix 'pol:v24:*' 'pol:v24'
purge_prefix 'beh:v24:*' 'beh:v24'
RESET_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')
engine_body=$(curl -sS -X POST "$ENGINE/demo/reset" -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" -d "{\"reset_id\":\"$RESET_ID\"}" -w '\n%{http_code}')
engine_code=${engine_body##*$'\n'}
if [ "$engine_code" != "200" ]; then
  echo "engine reset $engine_code" >&2
  echo "$engine_body" >&2
  exit 22
fi
bank_body=$(curl -sS -X POST "$BANK/demo/reset" -H "Content-Type: application/json" -d "{\"reset_id\":\"$RESET_ID\"}" -w '\n%{http_code}')
bank_code=${bank_body##*$'\n'}
if [ "$bank_code" != "200" ]; then
  echo "bank reset $bank_code" >&2
  echo "$bank_body" >&2
  exit 22
fi
echo ready
