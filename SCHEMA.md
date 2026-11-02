# Snapshot schema `wqi.snapshot/1`

A JSON object has `schema`, opaque stable `site` pseudonym, timezone-aware ISO 8601 `captured_start` and `captured_end`, `source` scalar facts, `coverage`, `jobs`, and `logs`. Capture end must follow start.

`source` accepts only `wordpress`, `php`, `action_scheduler`, and `disable_wp_cron` scalar facts. `coverage` has `action`, `cron`, and `log`, each with boolean `complete` and integer or null `source_count`. A complete part must match the exported row count. Partial parts expose source count when known; no absence inference follows from them.

Each job has `kind` (`action` or `cron`), unique string `id` within kind, `hook`, `group`, `status`, and timezone-aware `scheduled`. Action statuses: `pending`, `in-progress`, `complete`, `failed`, `canceled`. Optional action `claim_id` is an opaque decimal ID; `claim_created` is a timezone-aware timestamp when the default claims table supplies it. These do not prove a worker is stuck. Cron status: `scheduled`. Optional cron `recurrence`, SHA-256 `arg_fingerprint` (derived from WordPress’s opaque event key, never the arguments), and `callback_present` reflect the exporter bootstrap context only. Log rows contain `action_id`, `time`, and a redacted `message` marker. They must refer to an exported action.

No arguments, raw log messages, serialized PHP, URLs, paths, claim contents, or user details are part of the format. Each log has the fixed message `Log event (message omitted for privacy)`; only its action ID and time vary. Undeclared snapshot, source, coverage, job, and log fields are rejected before an incident or backup can be created. Hook names can nevertheless reveal information. All free text has a 512-character limit. Only schema 1 is supported; incompatible input is rejected.
