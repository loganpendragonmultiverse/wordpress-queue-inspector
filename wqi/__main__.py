from __future__ import annotations
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from .core import VERSION, compare, findings, load_incident, read_snapshot, report, save_incident

def main():
    p = argparse.ArgumentParser(prog="wqi", description="Offline WordPress queue incident evidence")
    sub = p.add_subparsers(dest="command", required=True)
    for command in ("inspect", "compare", "report"):
        q = sub.add_parser(command); q.add_argument("baseline")
        if command != "inspect": q.add_argument("followup", nargs="?")
        if command == "report":
            q.add_argument("--format", choices=("markdown", "html", "json"), default="markdown")
            q.add_argument("--notes", default="")
            q.add_argument("--output")
    q = sub.add_parser("new"); q.add_argument("directory"); q.add_argument("baseline"); q.add_argument("followup", nargs="?"); q.add_argument("--notes", default="")
    q = sub.add_parser("serve"); q.add_argument("incident"); q.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    try:
        if args.command == "new": save_incident(args.directory, args.baseline, args.followup, args.notes); print(args.directory)
        elif args.command == "serve": serve(args.incident, args.port)
        else:
            if Path(args.baseline).is_dir(): before, follow, notes = load_incident(args.baseline)
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
    before, after, notes = load_incident(path) if Path(path).is_dir() else (read_snapshot(path), None, "")
    if port < 1 or port > 65535: raise ValueError("port out of range")
    jobs = before["jobs"]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            from html import escape
            if self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost"):
                self.send_error(403); return
            url = urlsplit(self.path); qs = parse_qs(url.query)
            if url.path == "/report":
                body = report(before, after, notes, "html"); mime = "text/html; charset=utf-8"
            elif url.path == "/logs":
                page = max(1, min(100000, int(qs.get("page", ["1"])[0]) if qs.get("page", ["1"])[0].isdigit() else 1))
                logs = sorted(before["logs"], key=lambda log: log["time"])
                rows = logs[(page-1)*100:page*100]
                links = " ".join(f'<a href="/logs?page={n}">{n}</a>' for n in range(max(1,page-1), min((len(logs)+99)//100,page+1)+1))
                trs = "".join("<tr>" + "".join("<td>"+escape(str(log[k]))+"</td>" for k in ("time","action_id","message")) + "</tr>" for log in rows)
                body = f'<!doctype html><html lang="en"><meta charset="utf-8"><title>Action log timeline</title><main><h1>Action log timeline</h1><p>Recorded events only. Messages are omitted at collection. <a href="/">Jobs</a></p><nav aria-label="Pages">{links}</nav><table><thead><tr><th>Time</th><th>Action ID</th><th>Redacted event</th></tr></thead><tbody>{trs}</tbody></table></main>'
                mime = "text/html; charset=utf-8"
            elif url.path == "/":
                term = qs.get("q", [""])[0].casefold()[:100]
                page = max(1, min(100000, int(qs.get("page", ["1"])[0]) if qs.get("page", ["1"])[0].isdigit() else 1))
                subset = [j for j in jobs if term in (j["hook"] + " " + j.get("group", "") + " " + j["id"]).casefold()]
                rows = subset[(page-1)*100:page*100]
                links = " ".join(f'<a href="/?q={escape(term, quote=True)}&page={n}">{n}</a>' for n in range(max(1,page-1), min((len(subset)+99)//100,page+1)+1))
                trs = "".join("<tr>" + "".join("<td>"+escape(str(j.get(k,"")))+"</td>" for k in ("kind","id","hook","group","status","scheduled")) + "</tr>" for j in rows)
                body = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Queue Inspector</title><style>body{{font:1rem system-ui;max-width:90rem;margin:auto;padding:1rem;color:#17202a}}a{{color:#0645ad}}:focus-visible{{outline:3px solid orange}}table{{width:100%;border-collapse:collapse}}td,th{{padding:.5rem;border:1px solid #aaa;text-align:left}}.scroll{{overflow:auto}}</style><main><h1>WordPress Queue Inspector</h1><p>Site {escape(before['site'])} · captured {escape(before['captured_end'])}</p><p>{len(subset)} matching jobs · page {page} · <a href="/report">Incident report</a> · <a href="/logs">Action log timeline</a></p><form><label for="q">Search hook, group or ID</label> <input id="q" name="q" value="{escape(term,quote=True)}"><button>Search</button></form><nav aria-label="Pages">{links}</nav><div class="scroll"><table><thead><tr><th>Source</th><th>ID</th><th>Hook</th><th>Group</th><th>Status</th><th>Scheduled UTC</th></tr></thead><tbody>{trs}</tbody></table></div><h2>Findings</h2><ul>{''.join('<li>'+escape(f['rule']+': '+f['evidence']+' Uncertainty: '+f['uncertainty'])+'</li>' for f in findings(before))}</ul></main></html>'''
                mime = "text/html; charset=utf-8"
            else:
                self.send_error(404); return
            data = body.encode(); self.send_response(200); self.send_header("Content-Type", mime); self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'"); self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Queue Inspector {VERSION}: http://127.0.0.1:{port}/", flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__ == "__main__": main()
