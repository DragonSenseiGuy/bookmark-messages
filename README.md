# Bookmark Messages

Bookmark Messages pulls links you sent in a one-to-one iMessage conversation, classifies them, and puts the links worth keeping into a searchable web library.


## Use it

1. Log in as the admin.
2. Open Settings and generate an upload token.
3. Give Terminal Full Disk Access on your Mac.
4. Copy the command from Settings and replace the example E.164 phone number.

```sh
curl -fsSL https://bookmarks.example.com/cli | bash -s -- +15551234567
```

The command remembers its last successful upload locally and on the server. Add `--full` before the phone number to rescan the conversation. It sends URLs and a checkpoint, not messages or the Messages database.

![Bookmark Messages demo screenshot](https://cdn.hackclub.com/01a01c8f-9df4-77b6-afcb-91e04d012aae/Bookmark%20messages%20demo.png)

## How it works

The macOS uploader reads links from messages you sent in the selected direct conversation. Flask stores new URLs in SQLite, then fetches each page and asks an OpenAI-compatible model for a title, summary, tags, and keep decision. The admin can edit or retry results. Public viewing, when enabled, only returns kept links.

Built with Flask, SQLAlchemy, SQLite, Tailwind CSS, DaisyUI, and OpenRouter.

## Deploy with Dokploy

1. Create a Dokploy Compose service from this public repository and select `compose.yaml`.
2. Add the variables from `.env.example` in Dokploy. Generate `SECRET_KEY` with `python3 -c 'import secrets; print(secrets.token_hex(32))'`.
3. Deploy, then attach your subdomain to the `app` service on port `8000` and enable HTTPS.
4. Keep the `bookmark_data` named volume. It contains the account, links, settings, tokens, and upload checkpoints.
5. Open `/health` to check the app, then log in with the first-boot admin credentials.

`ADMIN_PASSWORD` only seeds a new database. Later password changes are stored as a hash in SQLite. Back up the named volume before replacing or deleting the service.