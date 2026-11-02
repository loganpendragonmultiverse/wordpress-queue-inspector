from __future__ import annotations
import argparse
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlsplit
from .core import VERSION, add_capture, backup_incident, compare, findings, load_history, read_snapshot, report, restore_incident, save_incident, update_notes

def main():
    p = argparse.ArgumentParser(prog="wqi", description="Offline WordPress queue incident evidence")
    sub = p.add_subparsers(dest="command", required=True)
    for command in ("inspect", "compare", "report"):
        q = sub.add_parser(command); q.add_argument("baseline")
        if command != "inspect": q.add_argument("followup", nargs="?")
        if command != "inspect":
            q.add_argument("--baseline-index", type=int, default=0)
            q.add_argument("--followup-index", type=int, default=-1)
        if command == "report":
            q.add_argument("--format", choices=("markdown", "html", "json"), default="markdown")
            q.add_argument("--notes", default="")
            q.add_argument("--output")
    q = sub.add_parser("new"); q.add_argument("directory"); q.add_argument("baseline"); q.add_argument("followup", nargs="?"); q.add_argument("--notes", default="")
    q = sub.add_parser("add"); q.add_argument("directory"); q.add_argument("snapshot")
    q = sub.add_parser("note"); q.add_argument("directory"); q.add_argument("text"); q.add_argument("--append", action="store_true")
    q = sub.add_parser("history"); q.add_argument("directory")
    q = sub.add_parser("backup"); q.add_argument("directory"); q.add_argument("archive")
    q = sub.add_parser("restore"); q.add_argument("archive"); q.add_argument("directory")
    q = sub.add_parser("serve"); q.add_argument("incident"); q.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    try:
        if args.command == "new": save_incident(args.directory, args.baseline, args.followup, args.notes); print(args.directory)
        elif args.command == "add": add_capture(args.directory, args.snapshot); print(args.directory)
        elif args.command == "note": update_notes(args.directory, args.text, args.append); print(args.directory)
        elif args.command == "backup": backup_incident(args.directory, args.archive); print(args.archive)
        elif args.command == "restore": restore_incident(args.archive, args.directory); print(args.directory)
        elif args.command == "history":
            captures, _ = load_history(args.directory)
            print(json.dumps([{"index":i,"start":x["captured_start"],"end":x["captured_end"],"coverage":x["coverage"]} for i,x in enumerate(captures)],indent=2))
        elif args.command == "serve": serve(args.incident, args.port)
        else:
            if Path(args.baseline).is_dir():
                captures, notes = load_history(args.baseline)
                if args.command != "inspect" and (not -len(captures) <= args.baseline_index < len(captures) or not -len(captures) <= args.followup_index < len(captures)):
                    raise ValueError("capture index out of range; use history to list captures")
                before = captures[args.baseline_index] if args.command != "inspect" else captures[0]
                follow = captures[args.followup_index] if args.command != "inspect" and len(captures)>1 else None
            else: before, follow, notes = read_snapshot(args.baseline), None, ""
            if getattr(args, "followup", None): follow = read_snapshot(args.followup)
            if args.command == "inspect": output = json.dumps({"site": before["site"], "coverage": before["coverage"], "findings": findings(before)}, indent=2)
            elif args.command == "compare":
                if follow is None: raise ValueError("follow-up snapshot required")
                output = json.dumps(compare(before, follow), indent=2)
            else: output = report(before, follow, args.notes or notes, args.format)
            if getattr(args, "output", None): Path(args.output).write_text(output, encoding="utf-8")
            else: print(output)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        p.exit(2, f"wqi: {error}\n")

def serve(path: str, port: int):
    captures, notes = load_history(path) if Path(path).is_dir() else ([read_snapshot(path)], "")
    if port < 1 or port > 65535: raise ValueError("port out of range")
    csrf = secrets.token_urlsafe(24)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if not Path(path).is_dir() or self.path != "/note" or self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost"):
                self.send_error(403); return
            origin = self.headers.get("Origin")
            if origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                self.send_error(403); return
            if not self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
                self.send_error(415); return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > 8192: raise ValueError()
                data = parse_qs(self.rfile.read(size).decode("utf-8"),keep_blank_values=True)
                if not secrets.compare_digest(data.get("csrf", [""])[0], csrf):
                    self.send_error(403); return
                update_notes(path, data.get("notes", [""])[0])
            except (ValueError, UnicodeDecodeError, OSError):
                self.send_error(400,"Invalid note"); return
            self.send_response(303);self.send_header("Location","/");self.send_header("Content-Length","0");self.end_headers()
        def do_GET(self):
            from html import escape
            if self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost"):
                self.send_error(403); return
            url = urlsplit(self.path); qs = parse_qs(url.query)
            download = None
            try:
                capture_index = int(qs.get("capture", ["0"])[0])
                if capture_index < 0 or capture_index >= len(captures): raise ValueError()
            except ValueError:
                self.send_error(400,"Invalid capture index"); return
            active = captures[capture_index]
            if url.path == "/ui.js":
                body = (Path(__file__).with_name("ui.js")).read_text(encoding="utf-8"); mime = "application/javascript; charset=utf-8"
            elif url.path == "/report":
                try:
                    baseline_index = int(qs.get("baseline", ["0"])[0]); followup_index = int(qs.get("followup", [str(len(captures)-1)])[0])
                    if baseline_index < 0 or followup_index < 0 or baseline_index >= len(captures) or followup_index >= len(captures) or (len(captures)>1 and baseline_index >= followup_index): raise ValueError()
                    format = qs.get("format", ["html"])[0]
                    if format not in ("html", "markdown", "json"): raise ValueError()
                except ValueError:
                    self.send_error(400,"Choose an earlier baseline, later follow-up and supported report format"); return
                current_notes = json.loads((Path(path)/"incident.json").read_text(encoding="utf-8")).get("notes","") if Path(path).is_dir() else notes
                body = report(captures[baseline_index], captures[followup_index] if len(captures)>1 else None, current_notes, format)
                mime = {"html":"text/html", "markdown":"text/markdown", "json":"application/json"}[format] + "; charset=utf-8"
                if format != "html": download = "wqi-report." + ("md" if format == "markdown" else "json")
            elif url.path == "/logs":
                page = max(1, min(100000, int(qs.get("page", ["1"])[0]) if qs.get("page", ["1"])[0].isdigit() else 1))
                logs = sorted(active["logs"], key=lambda log: log["time"])
                rows = logs[(page-1)*100:page*100]
                links = " ".join(f'<a href="/logs?capture={capture_index}&page={n}">{n}</a>' for n in range(max(1,page-1), min((len(logs)+99)//100,page+1)+1))
                trs = "".join("<tr>" + "".join("<td>"+escape(str(log[k]))+"</td>" for k in ("time","action_id","message")) + "</tr>" for log in rows)
                body = f'<!doctype html><html lang="en"><meta charset="utf-8"><title>Action log timeline</title><main><h1>Action log timeline</h1><p>Capture {capture_index}. Recorded events only. Messages are omitted at collection. <a href="/?capture={capture_index}">Jobs</a></p><nav aria-label="Pages">{links}</nav><table><thead><tr><th>Time</th><th>Action ID</th><th>Redacted event</th></tr></thead><tbody>{trs}</tbody></table></main>'
                mime = "text/html; charset=utf-8"
            elif url.path == "/":
                term = qs.get("q", [""])[0].casefold()[:100]
                page = max(1, min(100000, int(qs.get("page", ["1"])[0]) if qs.get("page", ["1"])[0].isdigit() else 1))
                subset = [j for j in active["jobs"] if term in (j["hook"] + " " + j.get("group", "") + " " + j["id"]).casefold()]
                rows = subset[(page-1)*100:page*100]
                links = " ".join(f'<a href="/?capture={capture_index}&q={quote_plus(term)}&page={n}">{n}</a>' for n in range(max(1,page-1), min((len(subset)+99)//100,page+1)+1))
                trs = "".join("<tr><td><input type='checkbox' class='job-select' aria-label='Select job " + escape(j['id'],quote=True) + "' value='" + escape(j['kind']+":"+j['id'],quote=True) + "'></td>" + "".join("<td>"+escape(str(j.get(k,"")))+"</td>" for k in ("kind","id","hook","group","status","scheduled")) + "</tr>" for j in rows)
                options_view = "".join(f'<option value="{i}"{" selected" if i==capture_index else ""}>{i}: {escape(c["captured_end"])}</option>' for i,c in enumerate(captures))
                view_form = f'<form action="/"><label for="capture">Viewing capture</label> <select id="capture" name="capture">{options_view}</select> <button>View</button></form>' if len(captures)>1 else ''
                options_before = "".join(f'<option value="{i}">{i}: {escape(c["captured_end"])}</option>' for i,c in enumerate(captures))
                options_after = "".join(f'<option value="{i}"{" selected" if i==len(captures)-1 else ""}>{i}: {escape(c["captured_end"])}</option>' for i,c in enumerate(captures))
                comparison_form = f'<h2>Compare captures</h2><form action="/report"><label for="baseline">Baseline</label><select id="baseline" name="baseline">{options_before}</select> <label for="followup">Follow-up</label><select id="followup" name="followup">{options_after}</select> <label for="format">Format</label><select id="format" name="format"><option value="html">HTML</option><option value="markdown">Markdown</option><option value="json">JSON</option></select> <button>View or download report</button></form>' if len(captures)>1 else ''
                note_form = ''
                if Path(path).is_dir():
                    current_notes = json.loads((Path(path)/"incident.json").read_text(encoding="utf-8")).get("notes","")
                    note_form = f'<h2>Operator notes</h2><form method="post" action="/note"><input type="hidden" name="csrf" value="{csrf}"><label for="notes">Observations and remediation</label><br><textarea id="notes" name="notes" rows="5" maxlength="4000" style="width:min(100%,50rem)">{escape(current_notes)}</textarea><br><button>Save notes</button></form>'
                body = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Queue Inspector</title><style>body{{font:1rem/1.5 system-ui;margin:0;background:#f3f6fb;color:#172333}}main{{max-width:84rem;margin:2rem auto;padding:2rem;background:white;border:1px solid #dbe3ee;border-radius:1rem;box-shadow:0 8px 30px #142d4e12}}h1{{margin-top:0;font-size:2rem}}h2{{margin-top:2rem}}a{{color:#064d99}}:focus-visible{{outline:3px solid #ef9c1a;outline-offset:2px}}input,select,button,textarea{{font:inherit;padding:.35rem .5rem;border:1px solid #9eb0c5;border-radius:.4rem}}button{{background:#145ca8;color:white;border-color:#145ca8;cursor:pointer}}table{{width:100%;border-collapse:collapse;margin-top:.5rem}}td,th{{padding:.6rem;border-bottom:1px solid #dbe3ee;text-align:left;white-space:nowrap}}th{{background:#eaf2fb}}tbody tr:nth-child(even){{background:#f8fafd}}.scroll{{overflow:auto}}nav a{{margin-right:.8rem}}@media(max-width:650px){{main{{margin:0;padding:1rem;border-radius:0}}}}</style><script src="/ui.js" defer></script><main data-site="{escape(active['site'],quote=True)}" data-capture="{escape(active['captured_start'],quote=True)}"><h1>WordPress Queue Inspector</h1><p>Site {escape(active['site'])} · captured {escape(active['captured_end'])}</p>{view_form}<p>{len(subset)} matching jobs · page {page} · <a href="/report">Incident report</a> · <a href="/report?format=markdown">Markdown</a> · <a href="/report?format=json">JSON</a> · <a href="/logs?capture={capture_index}">Action log timeline</a></p><p id="selection-count" role="status" aria-live="polite">Selection loads in this browser</p><form><input type="hidden" name="capture" value="{capture_index}"><label for="q">Search hook, group or ID</label> <input id="q" name="q" value="{escape(term,quote=True)}"><button>Search</button></form><nav aria-label="Pages">{links}</nav><div class="scroll"><table><thead><tr><th>Select</th><th>Source</th><th>ID</th><th>Hook</th><th>Group</th><th>Status</th><th>Scheduled UTC</th></tr></thead><tbody>{trs}</tbody></table></div>{comparison_form}{note_form}<h2>Findings</h2><ul>{''.join('<li>'+escape(f['rule']+': '+f['evidence']+' Uncertainty: '+f['uncertainty'])+'</li>' for f in findings(active))}</ul></main></html>'''
                mime = "text/html; charset=utf-8"
            else:
                self.send_error(404); return
            data = body.encode(); self.send_response(200); self.send_header("Content-Type", mime); self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'"); self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(data)))
            if download: self.send_header("Content-Disposition", f'attachment; filename="{download}"')
            self.end_headers(); self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Queue Inspector {VERSION}: http://127.0.0.1:{port}/", flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__ == "__main__": main()
