"""Small, fail-closed OpenAI-compatible forwarding service. No third-party deps."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

TICKS = 10_000_000_000


class Rejected(Exception):
    def __init__(self, message, status=400, code="invalid_request"):
        super().__init__(message)
        self.status, self.code = status, code


class Ledger:
    def __init__(self, path, cap_ticks=20*TICKS, zone="America/Toronto", now=None):
        self.path, self.cap = str(path), int(cap_ticks)
        self.zone = ZoneInfo(zone)
        self.now = now or (lambda: datetime.now(timezone.utc))
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS requests (
              id TEXT PRIMARY KEY, day TEXT NOT NULL, reserved INTEGER NOT NULL,
              actual INTEGER, status TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS control (id INTEGER PRIMARY KEY, paused INTEGER NOT NULL);
            INSERT OR IGNORE INTO control VALUES (1,0);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA busy_timeout=30000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def day(self):
        return self.now().astimezone(self.zone).date().isoformat()

    def snapshot(self):
        day = self.day()
        with self.connect() as db:
            total, actual, uncertain = db.execute(
                "SELECT COALESCE(SUM(COALESCE(actual,reserved)),0),"
                "COALESCE(SUM(actual),0), SUM(CASE WHEN actual IS NULL THEN 1 ELSE 0 END) "
                "FROM requests WHERE day=?", (day,)).fetchone()
            paused = bool(db.execute("SELECT paused FROM control WHERE id=1").fetchone()[0])
        return dict(day=day, timezone=self.zone.key, cap_usd=self.cap/TICKS,
                    committed_usd=total/TICKS, billed_usd=actual/TICKS,
                    remaining_usd=max(0,self.cap-total)/TICKS,
                    outstanding_reservations=uncertain or 0, paused=paused)

    def pause(self, paused):
        with self.connect() as db:
            db.execute("UPDATE control SET paused=? WHERE id=1", (bool(paused),))

    def reserve(self, ticks):
        if not isinstance(ticks, int) or ticks <= 0:
            raise Rejected("Invalid cost reservation")
        request_id, day = uuid.uuid4().hex, self.day()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT paused FROM control WHERE id=1").fetchone()[0]:
                raise Rejected("Paid work is paused by the operator.",402,"operator_paused")
            committed = db.execute("SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) "
                                   "FROM requests WHERE day=?",(day,)).fetchone()[0]
            if committed + ticks > self.cap:
                raise Rejected("Daily API budget has insufficient headroom. Work is saved; "
                               "try after the next Toronto midnight.",402,"budget_exhausted")
            db.execute("INSERT INTO requests VALUES (?,?,?,?,?,?)",
                       (request_id,day,ticks,None,"pending",time.time()))
        return request_id

    def settle(self, request_id, actual=None, status="uncertain"):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT reserved,actual FROM requests WHERE id=?",(request_id,)).fetchone()
            if not row:
                raise ValueError("Unknown reservation")
            if row[1] is not None:
                return  # settlement is idempotent
            if actual is not None and (type(actual) is not int or actual < 0):
                raise ValueError("Invalid billed cost")
            db.execute("UPDATE requests SET actual=?, status=? WHERE id=?",
                       (actual,status,request_id))
            if actual is not None and actual > row[0]:
                # Unexpected upstream pricing must not trigger more paid requests.
                db.execute("UPDATE control SET paused=1 WHERE id=1")


IMAGE_TYPES=("data:image/jpeg","data:image/png","data:image/webp","data:image/gif")
MAX_BODY_BYTES=8*1024*1024


def image_allowance(config):
    tokens,count,size=(int(config.get("image_tokens",2000)),
                       int(config.get("max_images",4)),
                       int(config.get("max_image_bytes",1_500_000)))
    if tokens<1 or count<1 or size<1:
        raise Rejected("Invalid image allowance")
    return tokens,count,size


def _check_image(part, max_bytes):
    image=part.get("image_url")
    url=image.get("url") if isinstance(image,dict) else None
    if not isinstance(url,str) or not url:
        raise Rejected("Invalid image. Resize it with jetson-image and send an image_url.")
    if not (url.startswith("https://") or url.startswith(IMAGE_TYPES)):
        raise Rejected("Images must be https URLs or resized JPEG, PNG, WebP, or GIF data URLs.")
    if len(url.encode())>max_bytes:
        raise Rejected("Image is too large. Resize it with jetson-image before sending.",413)
    return url


def _text_for_bound(payload):
    """Price image payloads as image tokens, not as one token per byte."""
    cloned=json.loads(json.dumps(payload))
    for message in cloned.get("messages") or []:
        content=message.get("content")
        if isinstance(content,list):
            for part in content:
                if isinstance(part,dict) and part.get("type")=="image_url" and isinstance(part.get("image_url"),dict):
                    part["image_url"]["url"]=""
    return cloned


def billed_ticks(usage, config):
    """Prefer a provider dollar field, otherwise price reported token counts."""
    if not isinstance(usage,dict):
        return None
    ticks=usage.get("cost_in_usd_ticks")
    if type(ticks) is int and ticks>=0:
        return ticks
    prompt,completion=usage.get("prompt_tokens"),usage.get("completion_tokens")
    if type(prompt) is int and type(completion) is int and prompt>=0 and completion>=0:
        return prompt*config["input_ticks_per_token"]+completion*config["output_ticks_per_token"]
    return None


def validate(payload, config):
    if not isinstance(payload, dict) or payload.get("model") != config["model"]:
        raise Rejected("Only the configured, priced model is available.")
    if payload.get("stream",False):
        raise Rejected("Streaming is disabled; use stream=false.")
    if payload.get("n",1) != 1:
        raise Rejected("Only one completion per request is supported.")
    allowed={"model","messages","stream","n","max_tokens","max_completion_tokens",
             "temperature","top_p","tools","tool_choice","parallel_tool_calls",
             "response_format","reasoning_effort","stop","seed","frequency_penalty",
             "presence_penalty","user","prompt_cache_key","prompt_cache_retention"}
    if set(payload)-allowed:
        raise Rejected("Request contains unsupported fields: "+", ".join(sorted(set(payload)-allowed)))
    messages = payload.get("messages")
    if not isinstance(messages,list) or not messages:
        raise Rejected("messages must be a nonempty list")
    image_tokens,max_images,max_image_bytes=image_allowance(config)
    images=0
    for message in messages:
        if not isinstance(message,dict):
            raise Rejected("Invalid message")
        content = message.get("content")
        if isinstance(content,list):
            for part in content:
                if not isinstance(part,dict):
                    raise Rejected("Invalid message content")
                kind=part.get("type")
                if kind=="text":
                    if not isinstance(part.get("text"),str):
                        raise Rejected("Invalid text part")
                elif kind=="image_url":
                    _check_image(part,max_image_bytes)
                    images+=1
                else:
                    raise Rejected("Only text and image_url inputs are enabled.")
        elif content is not None and not isinstance(content,str):
            raise Rejected("Invalid message content")
    if images>max_images:
        raise Rejected("Too many images. Send at most %d per request."%max_images,413)
    tools = payload.get("tools",[])
    if not isinstance(tools,list) or any(not isinstance(t,dict) or t.get("type") != "function" for t in tools):
        raise Rejected("Only locally executed function tools are enabled.")
    output = payload.get("max_completion_tokens",payload.get("max_tokens",config["max_output_tokens"]))
    if type(output) is not int or output < 1:
        raise Rejected("Invalid output limit.")
    output=min(output,config["max_output_tokens"])
    # UTF-8 bytes bound text-token counts conservatively. JSON includes all message,
    # tool-schema and argument text; add ample per-record chat-format overhead.
    encoded = json.dumps(_text_for_bound(payload),ensure_ascii=False,separators=(",",":")).encode()
    input_bound = len(encoded) + 64*(len(messages)+len(tools)) + 4096 + images*image_tokens
    if input_bound > config["max_input_tokens"]:
        raise Rejected("Input is too large. Compact the conversation or read the document in chunks.",413)
    forwarded = dict(payload)
    forwarded.pop("max_tokens",None)
    forwarded["max_completion_tokens"] = output
    forwarded["stream"] = False
    # Integer prices in ticks/token; reserve uncached rates, refund cache savings
    # only after the provider reports the actual bill.
    reservation = input_bound*config["input_ticks_per_token"] + output*config["output_ticks_per_token"]
    return forwarded, reservation


class Application:
    def __init__(self, config, ledger, token, key):
        self.config,self.ledger,self.token,self.key = config,ledger,token,key

    def snapshot(self):
        status=self.ledger.snapshot()
        status['max_request_reservation_usd']=(
            self.config['max_input_tokens']*self.config['input_ticks_per_token']+
            self.config['max_output_tokens']*self.config['output_ticks_per_token'])/TICKS
        return status

    def complete(self, payload):
        forwarded, cost = validate(payload,self.config)
        if not self.key:
            raise Rejected("Provider API key has not been configured.",503,"not_configured")
        ticket = self.ledger.reserve(cost)
        request = Request(self.config["upstream"].rstrip("/")+"/chat/completions",
                          data=json.dumps(forwarded).encode(), method="POST",
                          headers={"Authorization":"Bearer "+self.key,
                                   "Content-Type":"application/json", "User-Agent":"JetsonBot/1.0"})
        try:
            with urlopen(request,timeout=self.config.get("timeout_seconds",300)) as response:
                raw = response.read(16*1024*1024+1)
                if len(raw)>16*1024*1024:
                    raise ValueError("Response too large")
            parsed = json.loads(raw)
            actual = billed_ticks(parsed.get("usage") or {}, self.config)
            if actual is None:
                self.ledger.settle(ticket)  # retain full reservation
            else:
                self.ledger.settle(ticket,actual,"billed")
            return parsed
        except HTTPError as error:
            error.close()
            # Admission failures are unbilled. Other errors could follow actual inference.
            if error.code in (400,401,403,404,413,422,429):
                self.ledger.settle(ticket,0,"upstream_rejected")
            else:
                self.ledger.settle(ticket)
            raise Rejected("Model provider rejected the request (HTTP %s)."%error.code,
                           429 if error.code==429 else 502,"upstream_error") from None
        except (URLError,TimeoutError,OSError,ValueError):
            self.ledger.settle(ticket)
            raise Rejected("Model request outcome is uncertain; its maximum cost remains reserved.",
                           502,"upstream_uncertain") from None


class Handler(BaseHTTPRequestHandler):
    server_version = "JetsonBudget/1"

    def log_message(self, *_):
        pass  # never log request text, credentials, or provider error bodies

    def send_json(self, status, value):
        body=json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):
            pass

    def authorized(self):
        return hmac.compare_digest(self.headers.get("Authorization",""),"Bearer "+self.server.app.token)

    def do_GET(self):
        app = self.server.app
        if self.path == "/health":
            return self.send_json(200,dict(ok=True,configured=bool(app.key)))
        if not self.authorized():
            return self.send_json(401,{"error":{"message":"Unauthorized"}})
        if self.path == "/budget":
            return self.send_json(200,app.snapshot())
        if self.path == "/v1/models":
            return self.send_json(200,{"object":"list","data":[{"id":app.config["model"],"object":"model"}]})
        self.send_json(404,{"error":{"message":"Unknown endpoint"}})

    def do_POST(self):
        if not self.authorized():
            return self.send_json(401,{"error":{"message":"Unauthorized"}})
        try:
            length=int(self.headers.get("Content-Length","0"))
            if length<1 or length>MAX_BODY_BYTES:
                raise Rejected("Invalid body size",413)
            payload=json.loads(self.rfile.read(length))
            if self.path in ("/pause","/resume"):
                self.server.app.ledger.pause(self.path=="/pause")
                return self.send_json(200,self.server.app.snapshot())
            if self.path!="/v1/chat/completions":
                raise Rejected("Unknown endpoint",404)
            self.send_json(200,self.server.app.complete(payload))
        except Rejected as error:
            self.send_json(error.status,{"error":{"message":str(error),"type":"jetson_budget",
                                                 "code":error.code}})
        except (ValueError,TypeError):
            self.send_json(400,{"error":{"message":"Invalid request"}})
        except Exception:
            self.send_json(503,{"error":{"message":"Local budget service unavailable"}})


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",default="/etc/jetson-bot/budget.json")
    args=parser.parse_args()
    config=json.loads(Path(args.config).read_text())
    for name in ("input_ticks_per_token","output_ticks_per_token","max_input_tokens","max_output_tokens"):
        if type(config.get(name)) is not int or config[name]<=0:
            raise ValueError("Missing positive cost/input bound: "+name)
    if config["max_input_tokens"]>=200_000:
        raise ValueError("This price table requires input below the long-context threshold")
    token=Path(config["token_file"]).read_text().strip()
    key_path=Path(config["key_file"])
    key=key_path.read_text().strip() if key_path.exists() else ""
    if len(token)<32:
        raise ValueError("A strong local token is required")
    ledger=Ledger(config["database"],int(config.get("daily_cap_ticks",20*TICKS)),config.get("timezone","America/Toronto"))
    server=ThreadingHTTPServer(("0.0.0.0",8870),Handler)
    server.daemon_threads=True
    server.app=Application(config,ledger,token,key)
    print("Budget service ready; upstream configured="+str(bool(key)),flush=True)
    server.serve_forever()


if __name__=="__main__":
    main()
