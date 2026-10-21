"""Mission control as an AWS Lambda behind a function URL. Same App, AWS store."""

from __future__ import annotations

import base64
import os

from .app import App
from .store import AwsStore

_app = App(AwsStore(os.environ["TABLE"], os.environ["BUCKET"], os.environ.get("AWS_REGION", "us-east-1")),
           os.environ["TOKEN"])


def handler(event, _context):
    http = event["requestContext"]["http"]
    body = event.get("body") or ""
    raw = base64.b64decode(body) if event.get("isBase64Encoded") else body.encode()
    code, ctype, out = _app.handle(http["method"], http["path"], event.get("headers") or {}, raw,
                                   event.get("queryStringParameters") or {})
    return {"statusCode": code, "headers": {"Content-Type": ctype, "Cache-Control": "no-store"},
            "body": out.decode()}
