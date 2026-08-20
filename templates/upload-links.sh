#!/usr/bin/env bash
set -euo pipefail

SERVER_URL='{{ server_url }}'
FULL_SCAN=0
PHONE=''

usage() {
  echo "Usage: bash -s -- [--full] +15551234567" >&2
  exit 2
}

for argument in "$@"; do
  case "$argument" in
    --full) FULL_SCAN=1 ;;
    +*) PHONE="$argument" ;;
    *) usage ;;
  esac
done

[[ "$PHONE" =~ ^\+[1-9][0-9]{7,14}$ ]] || usage

for command in python3 sqlite3 curl; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Missing required command: $command" >&2
    exit 1
  fi
done

if [[ -z "${BOOKMARK_UPLOAD_TOKEN:-}" ]]; then
  printf 'Upload token: ' > /dev/tty
  IFS= read -r -s BOOKMARK_UPLOAD_TOKEN < /dev/tty
  printf '\n' > /dev/tty
fi

export SERVER_URL PHONE FULL_SCAN BOOKMARK_UPLOAD_TOKEN

python3 <<'PY'
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SERVER = os.environ["SERVER_URL"].rstrip("/")
PHONE = os.environ["PHONE"]
TOKEN = os.environ["BOOKMARK_UPLOAD_TOKEN"]
FULL_SCAN = os.environ["FULL_SCAN"] == "1"
CONTACT_ID = hashlib.sha256(PHONE.encode()).hexdigest()
STATE_PATH = Path.home() / ".config" / "bookmark-messages" / "state.json"
CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
URL_PATTERN = re.compile(r'(?:https?://|www\.)[^\s<>\"\)\]]+')


def api(path, method="GET", payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": "Bearer " + TOKEN}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(SERVER + path, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise RuntimeError(f"server returned {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"could not reach {SERVER}: {error.reason}") from error


def load_state():
    try:
        with STATE_PATH.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.chmod(0o600)
    temporary.replace(STATE_PATH)


def archived_text(blob):
    if not blob or not isinstance(blob, bytes) or b"NSString" not in blob:
        return ""
    try:
        position = blob.index(b"NSString")
        position = blob.index(b"+", position) + 1
        length = blob[position]
        position += 1
        if length == 0x81:
            length = int.from_bytes(blob[position:position + 2], "little")
            position += 2
        elif length == 0x82:
            length = int.from_bytes(blob[position:position + 4], "little")
            position += 4
        return blob[position:position + length].decode("utf-8", "ignore")
    except (ValueError, IndexError):
        return ""


if not CHAT_DB.exists():
    sys.exit(
        "Messages database not found. Run this on a Mac and grant your terminal "
        "Full Disk Access in System Settings."
    )

state = load_state()
state_key = SERVER + "|" + CONTACT_ID
local_cursor = state.get(state_key)
server_cursor = api("/api/upload/checkpoint/" + CONTACT_ID).get("message_date")
if FULL_SCAN or local_cursor is None or server_cursor is None:
    cursor = 0
else:
    cursor = min(int(local_cursor), int(server_cursor))

try:
    connection = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
except sqlite3.OperationalError as error:
    sys.exit(
        "Could not open Messages. Grant your terminal Full Disk Access, then try again. "
        f"({error})"
    )

with connection:
    handles = connection.execute("SELECT ROWID, id FROM handle").fetchall()
    matching_handles = [
        row_id
        for row_id, value in handles
        if re.sub(r"\D", "", value or "") == PHONE[1:]
    ]
    if not matching_handles:
        sys.exit("No Messages contact matches that E.164 phone number.")

    placeholders = ",".join("?" for _ in matching_handles)
    direct_chats = connection.execute(
        f"""
        SELECT chat_id
        FROM chat_handle_join
        GROUP BY chat_id
        HAVING COUNT(DISTINCT handle_id) = 1
           AND MAX(handle_id IN ({placeholders})) = 1
        """,
        matching_handles,
    ).fetchall()
    chat_ids = [row[0] for row in direct_chats]
    if not chat_ids:
        sys.exit("No one-to-one Messages conversation matches that phone number.")

    chat_placeholders = ",".join("?" for _ in chat_ids)
    rows = connection.execute(
        f"""
        SELECT m.date, m.text, m.attributedBody
        FROM message AS m
        JOIN chat_message_join AS cmj ON cmj.message_id = m.ROWID
        WHERE cmj.chat_id IN ({chat_placeholders})
          AND m.is_from_me = 1
          AND m.date > ?
        ORDER BY m.date ASC
        """,
        [*chat_ids, cursor],
    ).fetchall()

latest = max([cursor, *[int(row[0] or 0) for row in rows]])
urls = []
seen = set()
for _, text, attributed_body in rows:
    message = text or archived_text(attributed_body)
    for match in URL_PATTERN.findall(message or ""):
        url = match.rstrip(".,;:!?")
        if url not in seen:
            seen.add(url)
            urls.append(url)

result = api(
    "/api/upload",
    "POST",
    {"contact_id": CONTACT_ID, "message_date": latest, "urls": urls},
)
state[state_key] = result["message_date"]
save_state(state)

print(
    f"Uploaded {result['new']} new link(s); "
    f"{result['duplicates']} already existed."
)
link_ids = result["link_ids"]
if not link_ids:
    print("No links to classify.")
    raise SystemExit(0)

print("Waiting for server classification. Press Ctrl-C to detach.")
try:
    while True:
        status = api("/api/upload/status", "POST", {"link_ids": link_ids})
        states = status["states"]
        pending = sum(value == "pending" for value in states.values())
        failed = len(status["failed"])
        finished = sum(value in {"classified", "failed"} for value in states.values())
        print(
            f"\rClassified {finished}/{len(link_ids)}; {pending} pending; {failed} failed",
            end="",
            flush=True,
        )
        if finished == len(link_ids):
            print()
            if failed:
                print("AI model not available at this time")
                raise SystemExit(1)
            print("Done.")
            break
        time.sleep(2)
except KeyboardInterrupt:
    print("\nDetached. The server will keep classifying links.")
PY
