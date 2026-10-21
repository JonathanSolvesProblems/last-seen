"""Tear Last Seen down. Scheduled for 2026-11-10, the day after judging closes.

    python infra/teardown.py --profile lastseen            # dry run: lists what it would delete
    python infra/teardown.py --profile lastseen --yes      # deletes

Deletes the stack, empties and deletes the media and artifacts buckets, then
sweeps EVERY region for anything tagged project=lastseen that a stack delete
can leave behind (Lambda functions, log groups, buckets, tables), and reports
what is left. Run it once at deploy time with no --yes, so the day it matters
is not the first day it runs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import boto3

OUT = Path(__file__).resolve().parent / "outputs.json"


def empty_bucket(s3, name: str, go: bool) -> None:
    try:
        pages = s3.get_paginator("list_object_versions").paginate(Bucket=name)
        for p in pages:
            objs = [{"Key": o["Key"], "VersionId": o["VersionId"]}
                    for o in p.get("Versions", []) + p.get("DeleteMarkers", [])]
            if objs and go:
                s3.delete_objects(Bucket=name, Delete={"Objects": objs})
            print(f"  {'deleted' if go else 'would delete'} {len(objs)} objects in {name}")
        if go:
            s3.delete_bucket(Bucket=name)
            print(f"  deleted bucket {name}")
    except s3.exceptions.ClientError as e:
        print(f"  {name}: {e.response['Error']['Code']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--prefix", default="lastseen")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    o = json.loads(OUT.read_text()) if OUT.exists() else {}
    s = boto3.Session(profile_name=a.profile, region_name=o.get("region", "us-east-1"))
    s3, cfn = s.client("s3"), s.client("cloudformation")
    stack = o.get("stack", f"{a.prefix}-mission-control")
    print(f"{'TEARING DOWN' if a.yes else 'DRY RUN'} for project={a.prefix}")

    if o.get("Bucket"):
        empty_bucket(s3, o["Bucket"], a.yes)        # a non-empty bucket blocks the stack delete
    if a.yes:
        try:
            cfn.delete_stack(StackName=stack)
            cfn.get_waiter("stack_delete_complete").wait(StackName=stack)
            print(f"  deleted stack {stack}")
        except cfn.exceptions.ClientError as e:
            print(f"  stack {stack}: {e}")
    else:
        print(f"  would delete stack {stack}")
    if o.get("artifacts"):
        empty_bucket(s3, o["artifacts"], a.yes)

    print("sweep, every region:")
    leftovers = 0
    for region in s.get_available_regions("lambda"):
        try:
            tagging = s.client("resourcegroupstaggingapi", region_name=region)
            res = tagging.get_resources(TagFilters=[{"Key": "project", "Values": [a.prefix]}])
        except Exception as e:  # noqa: BLE001  (regions not enabled on the account)
            if "UnrecognizedClient" not in str(e) and "AuthFailure" not in str(e):
                print(f"  {region}: {str(e)[:80]}")
            continue
        for r in res.get("ResourceTagMappingList", []):
            leftovers += 1
            print(f"  LEFT: {r['ResourceARN']}")
    print(f"sweep done, {leftovers} tagged resources remain"
          + ("" if leftovers or not a.yes else "; also revoke the token and the lastseen access key"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
