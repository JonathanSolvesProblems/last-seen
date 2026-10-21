"""Where mission control keeps things. Memory (local) or DynamoDB + S3 (AWS)."""

from __future__ import annotations

import base64
import json
import threading
from decimal import Decimal
from typing import Any, Protocol


class Store(Protocol):
    def list_missions(self) -> list[dict]: ...
    def mission(self, m: str) -> dict: ...
    def set_state(self, m: str, state: dict) -> None: ...
    def set_field(self, m: str, key: str, value: Any) -> None: ...
    def add_question(self, m: str, q: dict) -> None: ...
    def question(self, m: str, q: str) -> dict | None: ...
    def decide(self, m: str, q: str, decision: str, at: float) -> bool: ...
    def add_pin(self, m: str, pin: dict) -> None: ...
    def put_image(self, m: str, name: str, b64: str) -> str: ...


def _blank(m: str) -> dict:
    return {"id": m, "state": None, "questions": [], "pins": [], "finished": None, "recall": None,
            "reference": None}


class MemoryStore:
    def __init__(self):
        self._m: dict[str, dict] = {}
        self._img: dict[str, str] = {}
        self._lock = threading.Lock()

    def _get(self, m: str) -> dict:
        return self._m.setdefault(m, _blank(m))

    def list_missions(self) -> list[dict]:
        with self._lock:
            return [{"id": k, "finished": v["finished"] is not None, "questions": len(v["questions"])}
                    for k, v in sorted(self._m.items(), reverse=True)]

    def mission(self, m: str) -> dict:
        with self._lock:
            d = json.loads(json.dumps(self._get(m)))
        for q in d["questions"]:
            if q.get("crop"):
                q["crop_data"] = self._img.get(q["crop"])
        if d.get("reference"):
            d["reference_data"] = self._img.get(d["reference"])
        return d

    def set_state(self, m: str, state: dict) -> None:
        with self._lock:
            self._get(m)["state"] = state

    def set_field(self, m: str, key: str, value: Any) -> None:
        with self._lock:
            self._get(m)[key] = value

    def add_question(self, m: str, q: dict) -> None:
        with self._lock:
            self._get(m)["questions"].append(q)

    def question(self, m: str, q: str) -> dict | None:
        with self._lock:
            return next((dict(x) for x in self._get(m)["questions"] if x["id"] == q), None)

    def decide(self, m: str, q: str, decision: str, at: float) -> bool:
        with self._lock:
            for x in self._get(m)["questions"]:
                if x["id"] == q:
                    if x["decision"] is not None:
                        return False
                    x["decision"], x["decided"] = decision, at
                    return True
            return False

    def add_pin(self, m: str, pin: dict) -> None:
        with self._lock:
            self._get(m)["pins"].append(pin)

    def put_image(self, m: str, name: str, b64: str) -> str:
        key = f"{m}/{name}"
        self._img[key] = b64
        return key


def _dec(o: Any) -> Any:
    """DynamoDB wants Decimal, not float."""
    if isinstance(o, float):
        return Decimal(str(o))
    if isinstance(o, dict):
        return {k: _dec(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_dec(v) for v in o]
    return o


def _undec(o: Any) -> Any:
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, dict):
        return {k: _undec(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_undec(v) for v in o]
    return o


class AwsStore:
    """One DynamoDB item per mission (pk = mission id), crops in a private S3
    bucket that expires them, served back to the page as short-lived presigned
    URLs. The decision is a conditional write, so two taps cannot both win."""

    def __init__(self, table: str, bucket: str, region: str = "us-east-1"):
        import boto3  # noqa: PLC0415
        self.t = boto3.resource("dynamodb", region_name=region).Table(table)
        self.s3 = boto3.client("s3", region_name=region)
        self.bucket = bucket

    def _get(self, m: str) -> dict:
        item = self.t.get_item(Key={"id": m}).get("Item")
        if item is None:
            item = _blank(m)
            self.t.put_item(Item=_dec(item))
        return _undec(item)

    def list_missions(self) -> list[dict]:
        items = self.t.scan(ProjectionExpression="id, finished").get("Items", [])
        return sorted(({"id": i["id"], "finished": i.get("finished") is not None} for i in items),
                      key=lambda x: x["id"], reverse=True)

    def mission(self, m: str) -> dict:
        d = self._get(m)
        for q in d["questions"]:
            if q.get("crop"):
                q["crop_url"] = self.s3.generate_presigned_url(
                    "get_object", Params={"Bucket": self.bucket, "Key": q["crop"]}, ExpiresIn=600)
        if d.get("reference"):
            d["reference_url"] = self.s3.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": d["reference"]}, ExpiresIn=600)
        return d

    def set_state(self, m: str, state: dict) -> None:
        self._get(m)
        self.t.update_item(Key={"id": m}, UpdateExpression="SET #s = :s",
                           ExpressionAttributeNames={"#s": "state"}, ExpressionAttributeValues={":s": _dec(state)})

    def set_field(self, m: str, key: str, value: Any) -> None:
        self._get(m)
        self.t.update_item(Key={"id": m}, UpdateExpression="SET #k = :v",
                           ExpressionAttributeNames={"#k": key}, ExpressionAttributeValues={":v": _dec(value)})

    def add_question(self, m: str, q: dict) -> None:
        self._get(m)
        self.t.update_item(Key={"id": m}, UpdateExpression="SET questions = list_append(questions, :q)",
                           ExpressionAttributeValues={":q": [_dec(q)]})

    def question(self, m: str, q: str) -> dict | None:
        return next((x for x in self._get(m)["questions"] if x["id"] == q), None)

    def decide(self, m: str, q: str, decision: str, at: float) -> bool:
        qs = self._get(m)["questions"]
        idx = next((i for i, x in enumerate(qs) if x["id"] == q), None)
        if idx is None:
            return False
        from botocore.exceptions import ClientError  # noqa: PLC0415
        try:
            self.t.update_item(
                Key={"id": m},
                UpdateExpression=f"SET questions[{idx}].decision = :d, questions[{idx}].decided = :t",
                ConditionExpression=f"attribute_type(questions[{idx}].decision, :null)",
                ExpressionAttributeValues={":d": decision, ":t": _dec(at), ":null": "NULL"})
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def add_pin(self, m: str, pin: dict) -> None:
        self._get(m)
        self.t.update_item(Key={"id": m}, UpdateExpression="SET pins = list_append(pins, :p)",
                           ExpressionAttributeValues={":p": [_dec(pin)]})

    def put_image(self, m: str, name: str, b64: str) -> str:
        key = f"crops/{m}/{name}"
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=base64.b64decode(b64), ContentType="image/jpeg")
        return key
