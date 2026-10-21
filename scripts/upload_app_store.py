"""Upload a pre-signed IPA; credentials are supplied only through Actions Secrets."""

import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import zipfile


def summary(message):
    print(message, flush=True)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as output:
        output.write(message + "\n\n")


def altool(*arguments):
    command = [
        "xcrun", "altool", *arguments,
        "--username", os.environ["ASC_APPLE_ID"],
        "--password", "@env:ASC_APP_SPECIFIC_PASSWORD",
        "--output-format", "json",
    ]
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=1500)
    print(result.stdout, flush=True)
    if result.returncode:
        raise RuntimeError(f"Apple {arguments[0]} failed (exit {result.returncode})")
    return result.stdout


def delivery_from_output(output):
    def find(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if re.sub(r"[^a-z]", "", key.lower()) == "deliveryid" and isinstance(item, str):
                    return item
                found = find(item)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = find(item)
                if found:
                    return found
        return None

    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", output):
        try:
            value, _ = decoder.raw_decode(output[match.start():])
        except ValueError:
            continue
        found = find(value)
        if found:
            return found
    match = re.search(r"(?i)delivery[ _-]?id[\s\"':=]+([0-9a-f-]{36})", output)
    return match.group(1) if match else None


def main():
    for name in ("ASC_APPLE_ID", "ASC_APP_SPECIFIC_PASSWORD", "ASC_TEAM_ID"):
        if not os.environ.get(name):
            raise RuntimeError(f"Missing GitHub Secret: {name}")
    mode = os.environ["UPLOAD_MODE"]
    delivery = os.environ.get("DELIVERY_ID", "").strip()
    if mode == "upload":
        asset_name = os.environ["ASSET_NAME"]
        if Path(asset_name).name != asset_name or not asset_name.endswith(".ipa"):
            raise RuntimeError("Invalid IPA asset filename")
        ipa = Path(os.environ["RUNNER_TEMP"]) / "ipa" / asset_name
        checksum = hashlib.sha256(ipa.read_bytes()).hexdigest()
        if checksum != os.environ["IPA_SHA256"].lower():
            raise RuntimeError("IPA checksum does not match the locally verified file")
        with zipfile.ZipFile(ipa) as archive:
            names = [name for name in archive.namelist()
                     if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", name)]
            if len(names) != 1:
                raise RuntimeError("IPA must contain exactly one main application")
            info = plistlib.loads(archive.read(names[0]))
        expected = {
            "CFBundleIdentifier": "free.app.shoot.paradise",
            "CFBundleShortVersionString": "1.1.0",
            "CFBundleVersion": "1.1.0",
        }
        for key, value in expected.items():
            if info.get(key) != value:
                raise RuntimeError(f"Unexpected {key}: {info.get(key)}")
        summary(f"Verified Shootopia 1.1.0 (build 1.1.0), SHA-256: `{checksum}`.")
        result = altool("--upload-app", "--file", str(ipa),
                        "--team-id", os.environ["ASC_TEAM_ID"])
        summary("Apple upload command completed successfully.")
        delivery = delivery_from_output(result)
        if not delivery:
            summary("Apple did not return a delivery ID. Processing must be checked in App Store Connect.")
            return
    elif mode != "status":
        raise RuntimeError("Invalid upload mode")
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", delivery or ""):
        raise RuntimeError("A valid Apple delivery UUID is required")
    summary(f"Apple delivery ID: `{delivery}`.")
    altool("--build-status", "--delivery-id", delivery, "--wait")
    summary("Apple processing query completed. See the returned Apple status in the job log.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"::error::{error}", flush=True)
        sys.exit(1)
