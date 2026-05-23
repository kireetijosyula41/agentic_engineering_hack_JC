from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

SEED_FOLDER_NAME = "Hackathon Seed Docs"
SEED_DOCS = [
    {
        "title": "UHC Commercial Medical and Drug Policies",
        "url": "https://www.uhcprovider.com/en/policies-protocols/commercial-policies/commercial-medical-drug-policies.html?rfid=UHCOContRD",
    },
    {
        "title": "UHC Prior Authorization and Advance Notification",
        "url": "https://www.uhcprovider.com/en/prior-auth-advance-notification.html",
    },
]


def parse_jsonish(output: str) -> dict:
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", output, flags=re.S)
        if not match:
            return {"raw_output": output}
        return json.loads(match.group(1))


def run_command(args: list[str], timeout: int = 60) -> dict:
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    return parse_jsonish(result.stdout)


def ensure_required_env() -> None:
    missing = [name for name in ("SENSO_API_KEY", "NIMBLE_API_KEY") if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


def ensure_seed_folder() -> str:
    existing = run_command(
        ["senso", "kb", "find", "--query", SEED_FOLDER_NAME, "--type", "folder", "--limit", "20", "--output", "json", "--quiet"]
    )
    for item in existing.get("nodes", []):
        if item.get("name") == SEED_FOLDER_NAME:
            return item["kb_node_id"]

    created = run_command(["senso", "kb", "create-folder", "--name", SEED_FOLDER_NAME, "--output", "json", "--quiet"])
    return created["kb_node_id"]


def content_exists(title: str) -> bool:
    existing = run_command(
        ["senso", "kb", "find", "--query", title, "--type", "content", "--limit", "20", "--output", "json", "--quiet"]
    )
    for item in existing.get("nodes", []):
        if item.get("name") == title:
            return True
    return False


def fetch_markdown(url: str) -> str:
    payload = run_command(
        [
            "nimble",
            "--format",
            "json",
            "extract",
            "--url",
            url,
            "--format",
            "markdown",
            "--request-timeout",
            "10000",
        ],
        timeout=40,
    )
    markdown = ((payload.get("data") or {}).get("markdown") or "").strip()
    if not markdown:
        raise RuntimeError(f"Nimble returned empty markdown for {url}")
    return markdown


def create_raw_doc(folder_id: str, title: str, markdown: str, url: str) -> dict:
    wrapped = "\n".join(
        [
            f"# {title}",
            "",
            f"Source URL: {url}",
            f"Fetched at: {datetime.now(timezone.utc).isoformat()}",
            "",
            markdown,
        ]
    )
    data = {
        "title": title,
        "text": wrapped,
        "kb_folder_node_id": folder_id,
        "tag_ids": [],
    }
    return run_command(["senso", "kb", "create-raw", "--data", json.dumps(data), "--output", "json", "--quiet"])


def main() -> None:
    ensure_required_env()
    folder_id = ensure_seed_folder()
    uploaded: list[dict] = []
    skipped: list[str] = []

    for doc in SEED_DOCS:
        title = doc["title"]
        url = doc["url"]
        if content_exists(title):
            skipped.append(title)
            continue
        markdown = fetch_markdown(url)
        created = create_raw_doc(folder_id, title, markdown, url)
        uploaded.append(
            {
                "title": title,
                "url": url,
                "kb_node_id": created.get("kb_node_id"),
                "content_id": created.get("content_id"),
            }
        )

    print(
        json.dumps(
            {
                "folder_name": SEED_FOLDER_NAME,
                "folder_id": folder_id,
                "uploaded": uploaded,
                "skipped": skipped,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
