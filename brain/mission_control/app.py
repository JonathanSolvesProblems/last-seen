"""Mission control: one request handler, two homes.

The same `handle(method, path, headers, body)` serves the page and the JSON API
whether it runs under the local stdlib server (server.py, used in the field
over `adb reverse` and on the bench) or as an AWS Lambda behind a function URL
(lambda_handler.py). Only the store differs: memory locally, DynamoDB and S3 on
AWS. So what is tested here is what is deployed.

The coordinator is the only person who can confirm an identity. The page shows
the candidate's best crop beside the reference photo and two buttons; nothing
else on this server can change a decision, and a decision cannot be changed
once made (a second answer to the same question is refused).

Every request needs the bearer token (or ?token= for the page itself). The
page carries no secrets and the crops are of a consenting test subject.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .store import Store

HERE = Path(__file__).resolve().parent


def _build_page() -> str:
    """The page, with the brand's assets inlined: one request, no asset hosting, and the
    same bytes locally and on Lambda. {{art:name}} -> a WebP data URI from assets/,
    {{svg:name}} -> the SVG's markup, {{icon}} -> the favicon as a data URI."""
    import base64  # noqa: PLC0415
    html = (HERE / "page.html").read_text(encoding="utf-8")
    assets = HERE / "assets"
    for f in assets.glob("*.webp"):
        html = html.replace("{{art:%s}}" % f.stem,
                            "data:image/webp;base64," + base64.b64encode(f.read_bytes()).decode())
    for f in assets.glob("*.svg"):
        svg = f.read_text(encoding="utf-8")
        html = html.replace("{{svg:%s}}" % f.stem, svg[svg.index("<svg"):])
        if f.stem == "icon":
            html = html.replace("{{icon}}", "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode())
    return html


PAGE = _build_page()

ROUTES = [
    ("GET", r"^/$", "page"),
    ("GET", r"^/api/missions$", "list_missions"),
    ("GET", r"^/api/missions/(?P<m>[\w-]+)$", "get_mission"),
    ("POST", r"^/api/missions/(?P<m>[\w-]+)/state$", "post_state"),
    ("POST", r"^/api/missions/(?P<m>[\w-]+)/questions$", "post_question"),
    ("GET", r"^/api/missions/(?P<m>[\w-]+)/questions/(?P<q>[\w-]+)$", "get_question"),
    ("POST", r"^/api/missions/(?P<m>[\w-]+)/questions/(?P<q>[\w-]+)/answer$", "answer"),
    ("POST", r"^/api/missions/(?P<m>[\w-]+)/pins$", "post_pin"),
    ("POST", r"^/api/missions/(?P<m>[\w-]+)/finish$", "finish"),
    ("POST", r"^/api/missions/(?P<m>[\w-]+)/recall$", "recall"),
]


class App:
    def __init__(self, store: Store, token: str):
        self.store = store
        self.token = token

    def handle(self, method: str, path: str, headers: dict[str, str], body: bytes,
               query: dict[str, str] | None = None) -> tuple[int, str, bytes]:
        query = query or {}
        for m, pat, name in ROUTES:
            match = re.match(pat, path)
            if m == method and match:
                if name != "page" and not self._authed(headers, query):
                    return self._json(401, {"isError": True, "errorCategory": "permission",
                                            "isRetryable": False, "message": "missing or wrong token"})
                if name == "page" and not self._authed(headers, query):
                    return 401, "text/plain", b"add ?token=... to the URL"
                try:
                    data = json.loads(body or b"{}") if method == "POST" else {}
                except json.JSONDecodeError:
                    return self._json(400, {"isError": True, "errorCategory": "validation",
                                            "isRetryable": False, "message": "body is not JSON"})
                return getattr(self, name)(data, **match.groupdict())
        return self._json(404, {"isError": True, "errorCategory": "validation",
                                "isRetryable": False, "message": f"no route {method} {path}"})

    def _authed(self, headers: dict[str, str], query: dict[str, str]) -> bool:
        h = {k.lower(): v for k, v in headers.items()}
        return h.get("authorization", "") == f"Bearer {self.token}" or query.get("token") == self.token

    @staticmethod
    def _json(code: int, obj: Any) -> tuple[int, str, bytes]:
        return code, "application/json", json.dumps(obj).encode()

    # ---- routes ---------------------------------------------------------------

    def page(self, _data: dict) -> tuple[int, str, bytes]:
        return 200, "text/html; charset=utf-8", PAGE.encode()

    def list_missions(self, _data: dict) -> tuple[int, str, bytes]:
        return self._json(200, self.store.list_missions())

    def get_mission(self, _data: dict, m: str) -> tuple[int, str, bytes]:
        return self._json(200, self.store.mission(m))

    def post_state(self, data: dict, m: str) -> tuple[int, str, bytes]:
        ref = data.pop("reference_jpeg_b64", None)
        if ref:
            self.store.set_field(m, "reference", self.store.put_image(m, "reference.jpg", ref))
        self.store.set_state(m, {**data, "received": time.time()})
        rec = self.store.mission(m).get("recall")
        return self._json(200, {"ok": True, "recall": rec})

    def post_question(self, data: dict, m: str) -> tuple[int, str, bytes]:
        qid = uuid.uuid4().hex[:10]
        crop = data.pop("crop_jpeg_b64", None)
        crop_ref = self.store.put_image(m, f"{qid}.jpg", crop) if crop else None
        self.store.add_question(m, {"id": qid, **data, "crop": crop_ref, "decision": None,
                                    "created": time.time()})
        return self._json(201, {"id": qid})

    def get_question(self, _data: dict, m: str, q: str) -> tuple[int, str, bytes]:
        found = self.store.question(m, q)
        if found is None:
            return self._json(404, {"isError": True, "errorCategory": "validation",
                                    "isRetryable": False, "message": f"no question {q}"})
        return self._json(200, found)

    def answer(self, data: dict, m: str, q: str) -> tuple[int, str, bytes]:
        d = data.get("decision")
        if d not in ("confirmed", "rejected"):
            return self._json(400, {"isError": True, "errorCategory": "validation",
                                    "isRetryable": False, "message": "decision must be confirmed or rejected"})
        ok = self.store.decide(m, q, d, time.time())
        if not ok:
            return self._json(409, {"isError": True, "errorCategory": "business", "isRetryable": False,
                                    "message": "this question was already answered"})
        return self._json(200, {"ok": True, "decision": d})

    def post_pin(self, data: dict, m: str) -> tuple[int, str, bytes]:
        self.store.add_pin(m, data)
        return self._json(201, {"ok": True})

    def finish(self, data: dict, m: str) -> tuple[int, str, bytes]:
        self.store.set_field(m, "finished", {**data, "at": time.time()})
        return self._json(200, {"ok": True})

    def recall(self, _data: dict, m: str) -> tuple[int, str, bytes]:
        """The coordinator asks for the aircraft back. The field laptop reads this
        on its next state post and ends the mission; it is a request, the pilot's
        switch remains the authority."""
        self.store.set_field(m, "recall", {"at": time.time()})
        return self._json(200, {"ok": True})
