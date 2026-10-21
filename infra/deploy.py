"""Deploy Last Seen mission control to AWS. Always with --profile, never the default.

    python infra/deploy.py --profile lastseen

Creates (or reuses) an artifacts bucket, zips brain/mission_control, uploads it
under a content-hash key so every code change is a real Lambda update, deploys
infra/lastseen.yaml, and writes infra/outputs.json (git-ignored) with the URL
and the token. Prints the page link.

Teardown is infra/teardown.py, dated for 2026-11-10 (judging closes Nov 9).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import secrets
import sys
import zipfile
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "brain" / "mission_control"
OUT = Path(__file__).resolve().parent / "outputs.json"


def build_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        files = [f for f in sorted(SRC.iterdir()) if f.suffix in (".py", ".html")]
        files += sorted((SRC / "assets").glob("*")) if (SRC / "assets").exists() else []
        for f in files:
            # fixed timestamp, so the same source always hashes the same
            info = zipfile.ZipInfo(f"mission_control/{f.relative_to(SRC).as_posix()}", date_time=(2026, 1, 1, 0, 0, 0))
            z.writestr(info, f.read_bytes())
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--prefix", default="lastseen")
    a = ap.parse_args()
    s = boto3.Session(profile_name=a.profile, region_name=a.region)
    acct = s.client("sts").get_caller_identity()["Account"]
    s3, cfn = s.client("s3"), s.client("cloudformation")

    art = f"{a.prefix}-artifacts-{acct}"
    try:
        s3.head_bucket(Bucket=art)
    except s3.exceptions.ClientError:
        kw = {} if a.region == "us-east-1" else {"CreateBucketConfiguration": {"LocationConstraint": a.region}}
        s3.create_bucket(Bucket=art, **kw)
        s3.put_public_access_block(Bucket=art, PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True})
        s3.put_bucket_tagging(Bucket=art, Tagging={"TagSet": [{"Key": "project", "Value": a.prefix}]})

    blob = build_zip()
    key = f"mission-control/{hashlib.sha256(blob).hexdigest()[:16]}.zip"
    s3.put_object(Bucket=art, Key=key, Body=blob)

    prev = json.loads(OUT.read_text()) if OUT.exists() else {}
    token = prev.get("token") or secrets.token_urlsafe(24)
    stack = f"{a.prefix}-mission-control"
    params = [{"ParameterKey": k, "ParameterValue": v} for k, v in
              {"Prefix": a.prefix, "CodeKey": key, "ArtifactsBucket": art, "Token": token}.items()]
    body = (Path(__file__).resolve().parent / "lastseen.yaml").read_text()
    try:
        cfn.describe_stacks(StackName=stack)
        try:
            cfn.update_stack(StackName=stack, TemplateBody=body, Parameters=params,
                             Capabilities=["CAPABILITY_IAM"])
            cfn.get_waiter("stack_update_complete").wait(StackName=stack)
        except cfn.exceptions.ClientError as e:
            if "No updates" not in str(e):
                raise
    except cfn.exceptions.ClientError:
        cfn.create_stack(StackName=stack, TemplateBody=body, Parameters=params,
                         Capabilities=["CAPABILITY_IAM"], Tags=[{"Key": "project", "Value": a.prefix}])
        cfn.get_waiter("stack_create_complete").wait(StackName=stack)

    outs = {o["OutputKey"]: o["OutputValue"] for o in cfn.describe_stacks(StackName=stack)["Stacks"][0]["Outputs"]}
    OUT.write_text(json.dumps({**outs, "token": token, "profile": a.profile, "region": a.region,
                               "artifacts": art, "stack": stack, "teardown_on": "2026-11-10"}, indent=2))
    print(f"mission control: {outs['Url']}?token={token}")
    print(f"field laptop:    --coordinator mission-control --mission-control {outs['Url'].rstrip('/')} --token <outputs.json>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
