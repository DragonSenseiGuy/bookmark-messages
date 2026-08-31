# Bookmark Messages

Bookmark Messages gets links that you forward to yourself on messages and puts it on a nice dashboard for you to view.

## Install it

Just use docker, Dokploy deployment steps below!

## Use it

1. Log in as the admin.
2. Open Settings and generate an upload token.
3. Give your preffered terminal Full Disk Access on your Mac.
4. Copy the command from Settings and replace the example phone number.

```sh
curl -fsSL https://bookmarks.yourdomain.com/cli | bash -s -- +15551234567
```

![Bookmark Messages demo screenshot](https://cdn.hackclub.com/01a01c8f-9df4-77b6-afcb-91e04d012aae/Bookmark%20messages%20demo.png)

## Deploy with Dokploy

1. Create a Dokploy Compose service from this repo and select `compose.yaml` as the compose file.
2. Add the variables from `.env.example` in Dokploy. Generate a secret hex 32 key and put it in `SECRET_KEY`.
3. Deploy, then attach your subdomain to the `app` service on port `8000` and enable HTTPS.
4. Keep the `bookmark_data` named volume. It contains the account, links, settings, tokens, and upload checkpoints.
5. Open `/health` to check the app, then log in with the first-boot admin credentials.

# AI Usage 
Most of it was handcoded, for some parts I used help from AI. The last few features were made fully by AI but were throughly reviewed by me.
