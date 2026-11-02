"""Versioned, deliberately conservative WordPress queue evidence engine."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.0.0"
MAX_BYTES = 64 * 1024 * 1024
MAX_ITEMS = 100_000
MAX_LOGS = 500_000
VALID_STATUS = {"pending", "in-progress", "complete", "failed", "canceled"}
SAFE_TEXT = re.compile(r"^[^\x00-\x08\x0b\x0c\x0e-\x1f]{0,512}$")


def iso(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO 8601 string")
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"invalid timestamp: {value[:40]}") from error
    if date.tzinfo is None:
        raise ValueError("timestamp needs a timezone offset")
    return date.astimezone(timezone.utc)


def clean_text(value, label):
    if not isinstance(value, str) or not SAFE_TEXT.fullmatch(value):
        raise ValueError(f"{label} must be a short text value")
    return value


def validate(snapshot: dict) -> dict:
    if not isinstance(snapshot, dict) or snapshot.get("schema") != "wqi.snapshot/1":
        raise ValueError("unsupported snapshot schema; expected wqi.snapshot/1")
    for field in ("site", "captured_start", "captured_end", "source"):
        if field not in snapshot:
            raise ValueError(f"missing {field}")
    clean_text(snapshot["site"], "site")
    if not snapshot["site"]:
        raise ValueError("site pseudonym is required")
    start, end = iso(snapshot["captured_start"]), iso(snapshot["captured_end"])
    if end < start:
        raise ValueError("capture end precedes start")
    if not isinstance(snapshot["source"], dict):
        raise ValueError("source must be an object")
    for key, value in snapshot["source"].items():
        clean_text(key, "source key")
        if isinstance(value, str):
            clean_text(value, "source value")
        elif value is not None and not isinstance(value, (bool, int)):
            raise ValueError("source values must be scalar")
    jobs, logs = snapshot.get("jobs"), snapshot.get("logs")
    if not isinstance(jobs, list) or len(jobs) > MAX_ITEMS:
        raise ValueError("jobs must be a list of at most 100000")
    if not isinstance(logs, list) or len(logs) > MAX_LOGS:
        raise ValueError("logs must be a list of at most 500000")
    seen = set()
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError("job must be an object")
        kind = job.get("kind")
        if kind not in ("action", "cron"):
            raise ValueError("job kind must be action or cron")
        identity = clean_text(job.get("id"), "job id")
        if not identity:
            raise ValueError("job id is required")
        if (kind, identity) in seen:
            raise ValueError(f"duplicate job identity: {kind}/{identity}")
        seen.add((kind, identity))
        clean_text(job.get("hook"), "hook")
        clean_text(job.get("group", ""), "group")
        iso(job.get("scheduled"))
        if kind == "action" and job.get("status") not in VALID_STATUS:
            raise ValueError("invalid action status")
        if kind == "cron" and job.get("status") != "scheduled":
            raise ValueError("cron status must be scheduled")
        if "recurrence" in job:
            clean_text(job["recurrence"], "recurrence")
        if "callback_present" in job and not isinstance(job["callback_present"], bool):
            raise ValueError("callback_present must be boolean")
    for log in logs:
        if not isinstance(log, dict):
            raise ValueError("log must be an object")
        clean_text(log.get("action_id"), "log action_id")
        clean_text(log.get("message", ""), "log message")
        iso(log.get("time"))
        if ("action", log["action_id"]) not in seen:
            raise ValueError("log references an absent action")
    coverage = snapshot.get("coverage")
    if not isinstance(coverage, dict):
        raise ValueError("coverage object is required")
    for kind in ("action", "cron", "log"):
        part = coverage.get(kind)
        if not isinstance(part, dict) or not isinstance(part.get("complete"), bool):
            raise ValueError(f"coverage.{kind}.complete is required")
        count = part.get("source_count")
        if count is not None and (not isinstance(count, int) or count < 0):
            raise ValueError(f"coverage.{kind}.source_count must be a nonnegative integer or null")
        actual = len(logs) if kind == "log" else sum(j["kind"] == kind for j in jobs)
        if part["complete"] and count != actual:
            raise ValueError(f"coverage.{kind} claims complete but count differs: {count} vs {actual}")
        if count is not None and actual > count:
            raise ValueError(f"coverage.{kind} exports more than source_count")
    return snapshot


def read_snapshot(path: str | Path) -> dict:
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("snapshot exceeds 64 MiB limit")
    return validate(json.loads(path.read_text(encoding="utf-8")))


def fingerprint(job):
    if job["kind"] == "action":
        return ("action", job["id"])
    return ("cron", job["hook"], job.get("recurrence", ""), job.get("group", ""))


def findings(snapshot: dict, overdue_minutes=60, repeat_failures=2) -> list[dict]:
    validate(snapshot)
    now = iso(snapshot["captured_end"])
    result = []
    failures = Counter()
    for job in snapshot["jobs"]:
        delta = (now - iso(job["scheduled"])).total_seconds() / 60
        if job["status"] in ("pending", "scheduled") and delta > overdue_minutes:
            result.append({"rule": "overdue", "job": job["id"], "evidence": f"scheduled {round(delta)} minutes before capture end; threshold {overdue_minutes}", "uncertainty": "A queued job can be healthy with a slow or external runner; scheduling alone does not establish cause."})
        if job["status"] == "failed":
            failures[job["hook"]] += 1
        if job.get("callback_present") is False:
            result.append({"rule": "missing-callback", "job": job["id"], "evidence": "No registered callback observed during export", "uncertainty": "Bootstrap context may differ from the runner."})
    for hook, count in failures.items():
        if count >= repeat_failures:
            result.append({"rule": "repeated-failures", "job": "", "evidence": f"{count} failed actions for hook {hook}; threshold {repeat_failures}", "uncertainty": "Status does not establish root cause or plugin ownership."})
    if any(not snapshot["coverage"][k]["complete"] for k in ("action", "cron", "log")):
        result.append({"rule": "partial-capture", "job": "", "evidence": "One or more source counts or pagination bounds are incomplete", "uncertainty": "Absence and totals cannot be interpreted as whole-site evidence."})
    return result


def compare(before: dict, after: dict) -> dict:
    validate(before); validate(after)
    if before["site"] != after["site"]:
        raise ValueError("cannot compare different site pseudonyms")
    if iso(after["captured_start"]) < iso(before["captured_end"]):
        raise ValueError("follow-up capture overlaps or precedes baseline")
    old, new = defaultdict(list), defaultdict(list)
    for job in before["jobs"]: old[fingerprint(job)].append(job)
    for job in after["jobs"]: new[fingerprint(job)].append(job)
    rows = []
    complete = all(before["coverage"][k]["complete"] and after["coverage"][k]["complete"] for k in ("action", "cron"))
    for key in old.keys() | new.keys():
        a, b = old[key], new[key]
        if len(a) > 1 or len(b) > 1:
            rows.append({"identity": str(key), "change": "ambiguous", "detail": "Multiple recurring events share a fingerprint; no pairing inferred."})
            continue
        if not a:
            change = "new" if complete else "observed-only-after"
        elif not b:
            change = "no-longer-observed" if complete else "unknown-partial-capture"
        else:
            change = "unchanged" if a[0]["status"] == b[0]["status"] else "progressed" if b[0]["status"] == "complete" else "changed"
        rows.append({"identity": str(key), "change": change, "before": a[0]["status"] if a else None, "after": b[0]["status"] if b else None})
    return {"site": before["site"], "complete_scope": complete, "rows": sorted(rows, key=lambda r: r["identity"]), "caution": "No-longer-observed never proves completion. Actions can be deleted and logs pruned."}


def report(before: dict, after: dict | None = None, notes: str = "", format: str = "markdown") -> str:
    analysis = findings(before)
    change = compare(before, after) if after else None
    lines = ["# WordPress Queue Inspector incident", "", f"Site: {before['site']}", f"Baseline: {before['captured_start']} to {before['captured_end']}", f"Follow-up: {after['captured_start']} to {after['captured_end']}" if after else "Follow-up: not supplied", "", "## Coverage"]
    for name, part in before["coverage"].items():
        lines.append(f"- {name}: {'complete' if part['complete'] else 'partial'}; source count {part['source_count']}")
    lines += ["", "## Findings"]
    lines += [f"- {item['rule']} {item['job']}: {item['evidence']}. Uncertainty: {item['uncertainty']}" for item in analysis] or ["- No finding under the configured rules; this does not establish health."]
    if change:
        lines += ["", "## Comparison", f"Scope complete: {change['complete_scope']}", change["caution"]]
        lines += [f"- {r['identity']}: {r['change']} ({r.get('before')} → {r.get('after')})" for r in change["rows"]]
    lines += ["", "## Operator observations", notes or "None supplied.", "", "## Handoff checklist", "- Confirm runner configuration independently.", "- Review source coverage, timestamps and missing logs.", "- Verify repair effects using direct site evidence."]
    if format == "markdown": return "\n".join(lines) + "\n"
    if format == "html": return "<!doctype html><meta charset='utf-8'><title>Queue incident</title><main><pre>" + html.escape("\n".join(lines)) + "</pre></main>"
    if format == "json": return json.dumps({"baseline": {k: before[k] for k in ("site", "captured_start", "captured_end", "coverage")}, "findings": analysis, "comparison": change, "notes": notes}, indent=2) + "\n"
    raise ValueError("format must be markdown, html or json")


def save_incident(directory: str | Path, before_path: str | Path, after_path: str | Path | None = None, notes: str = ""):
    directory = Path(directory)
    if directory.exists(): raise ValueError("incident directory already exists")
    before = read_snapshot(before_path)
    after = read_snapshot(after_path) if after_path else None
    if after: compare(before, after)
    directory.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".wqi-", dir=directory.parent))
    try:
        for name, path in (("baseline.json", before_path), ("followup.json", after_path)):
            if path:
                shutil.copyfile(path, temp / name)
        manifest = {"schema": "wqi.incident/1", "version": VERSION, "notes": notes, "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in temp.iterdir()}}
        (temp / "incident.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(temp, directory)
    finally:
        if temp.exists(): shutil.rmtree(temp)


def load_incident(directory: str | Path):
    directory = Path(directory)
    manifest = json.loads((directory / "incident.json").read_text())
    if manifest.get("schema") != "wqi.incident/1": raise ValueError("unsupported incident format")
    for name, digest in manifest["files"].items():
        if name not in ("baseline.json", "followup.json") or hashlib.sha256((directory / name).read_bytes()).hexdigest() != digest:
            raise ValueError("incident hash mismatch")
    return read_snapshot(directory / "baseline.json"), read_snapshot(directory / "followup.json") if "followup.json" in manifest["files"] else None, manifest["notes"]
