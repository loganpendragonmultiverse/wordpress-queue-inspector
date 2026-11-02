# Snapshot schema `wqi.snapshot/1`

A JSON object has `schema`, opaque stable `site` pseudonym, timezone-aware ISO 8601 `captured_start` and `captured_end`, `source` scalar facts, `coverage`, `jobs`, and `logs`. Capture end must follow start.

`coverage` has `action`, `cron`, and `log`, each with boolean `complete` and integer or null `source_count`. A complete part must match the exported row count. Partial parts expose source count when known; no absence inference follows from them.

Each job has `kind` (`action` or `cron`), unique string `id` within kind, `hook`, `group`, `status`, and timezone-aware `scheduled`. Action statuses: `pending`, `in-progress`, `complete`, `failed`, `canceled`. Cron status: `scheduled`. Optional cron `recurrence` and `callback_present` reflect the exporter bootstrap context only. Log rows contain `action_id`, `time`, and a redacted `message` marker. They must refer to an exported action.

No arguments, log messages, serialized PHP, URLs, paths, claims, or user details are part of the format. Hook names can nevertheless reveal information. All free text has a 512-character limit. Only schema 1 is supported; incompatible input is rejected.
