#!/bin/sh
# Dừng đồng hồ, xoá dữ liệu demo sau 20/9, nạp lại RDB và persona, đưa đồng hồ về 09:00.
set -eu
BANK=${BANK_URL:-http://127.0.0.1:8092}
ENGINE=${ENGINE_URL:-http://127.0.0.1:8091}
DATABASE_URL=${DATABASE_URL:-postgresql://hainamtran@localhost:5432/fides_w1}
REDIS_URL=${REDIS_URL:-redis://localhost:6379/15}
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
TRUNCATE bank.entries, bank.transfers, bank.products, bank.guardian_requests,
         bank.guardians, bank.beneficiaries, bank.accounts, bank.idempotency;
SQL

# DEBUG RELOAD bị tắt. Tắt Redis không ghi đè file, rồi bật lại để nạp đúng RDB.
redis-cli SHUTDOWN NOSAVE >/dev/null 2>&1 || true
REDIS_SERVER=${REDIS_SERVER:-redis-server}
REDIS_CONF=${REDIS_CONF:-/opt/homebrew/etc/redis.conf}
"$REDIS_SERVER" "$REDIS_CONF" --daemonize yes
i=0
while true; do
  out=$(redis-cli -n "$DB" DBSIZE 2>&1 || true)
  case "$out" in
    *ERR*|*LOADING*|*loading*)
      i=$((i + 1))
      if [ "$i" -gt 120 ]; then
        echo "redis không lên: $out" >&2
        exit 1
      fi
      sleep 0.5
      ;;
    *)
      break
      ;;
  esac
done
engine_body=$(curl -sS -X POST "$ENGINE/demo/reset" -H "X-API-Key: $API_KEY" -H "Content-Length: 0" -w '\n%{http_code}')
engine_code=${engine_body##*$'\n'}
if [ "$engine_code" != "200" ]; then
  echo "engine reset $engine_code" >&2
  echo "$engine_body" >&2
  exit 22
fi
bank_body=$(curl -sS -X POST "$BANK/demo/reset" -w '\n%{http_code}')
bank_code=${bank_body##*$'\n'}
if [ "$bank_code" != "200" ]; then
  echo "bank reset $bank_code" >&2
  echo "$bank_body" >&2
  exit 22
fi
echo ready
