# engine

Chỗ đứng của FIDES Engine. `GET /health` và `POST /events` ghi sổ Postgres, idempotent theo `event_id`. Hợp đồng nằm ở `docs/openapi.yaml`. Thiếu `FIDES_HMAC_KEY` hoặc `FIDES_API_KEY` thì quá trình không khởi động.
