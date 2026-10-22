"""A stand-in for the Anthropic Messages API on 127.0.0.1, so the e2e API can run with FAKE_LLM=false
and ANTHROPIC_BASE_URL pointing here. Never forwards anything anywhere. Logs every request."""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1])
LOG = sys.argv[2]
REPLY = "Fake Claude here: noted."


def sample(schema, defs):
    if "$ref" in schema:
        return sample(defs[schema["$ref"].split("/")[-1]], defs)
    if "anyOf" in schema:
        return sample(schema["anyOf"][0], defs)
    if "enum" in schema:
        return schema["enum"][0]
    t = schema.get("type")
    if isinstance(t, list):
        t = t[0]
    if t == "object":
        props = schema.get("properties", {})
        return {k: sample(v, defs) for k, v in props.items()}
    if t == "array":
        return []
    if t == "string":
        return "fake"
    if t in ("number", "integer"):
        return 0.5 if t == "number" else 0
    if t == "boolean":
        return False
    return None


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        with open(LOG, "a") as f:
            f.write(json.dumps({"method": "GET", "path": self.path}) + "\n")
        body = json.dumps({"data": [{"id": "claude-haiku-5-5", "type": "model"}], "has_more": False}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        req = json.loads(self.rfile.read(n) or b"{}")
        with open(LOG, "a") as f:
            f.write(json.dumps({"method": "POST", "path": self.path, "model": req.get("model"),
                                "stream": bool(req.get("stream")), "key": (self.headers.get("x-api-key") or "")[:12]}) + "\n")
        fmt = (req.get("output_config") or {}).get("format")
        text = json.dumps(sample(fmt["schema"], fmt["schema"].get("$defs", {}))) if fmt else REPLY
        usage = {"input_tokens": 2000, "output_tokens": 200, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        msg = {"id": "msg_fake", "type": "message", "role": "assistant", "model": req.get("model"),
               "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
               "usage": usage}
        if not req.get("stream"):
            body = json.dumps(msg).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("request-id", "req_fake")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()

        def ev(name, data):
            self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()

        start = dict(msg, content=[], stop_reason=None, usage=dict(usage, output_tokens=1))
        ev("message_start", {"type": "message_start", "message": start})
        ev("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
        ev("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}})
        ev("content_block_stop", {"type": "content_block_stop", "index": 0})
        ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                             "usage": {"output_tokens": 200}})
        ev("message_stop", {"type": "message_stop"})


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
