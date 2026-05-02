# Capturing a TCDD API token

The CLI requires a JWT lifted from TCDD's public web client. Tokens are
not handed out via any API; you grab one from your own browser session.

## Steps (Chrome / Edge / Firefox)

1. Open <https://ebilet.tcddtasimacilik.gov.tr/> in a browser.
2. Open DevTools (`F12`, or `Cmd+Option+I` on macOS, `Ctrl+Shift+I` on
   Windows/Linux). Switch to the **Network** tab.
3. Make sure recording is on (the round icon at the top-left of the
   Network panel is red). Leave "Preserve log" unchecked.
4. On the page, search for any train (e.g. Ankara → İstanbul, any future
   date). This triggers the API call we need.
5. In the Network panel's filter box, type `train-availability`. One
   `POST` request should appear.
6. Click that request. In the right-hand pane, open **Headers** →
   **Request Headers**.
7. Find the `Authorization` header. Its value is a long string starting
   with `eyJ...` — that's the JWT. Copy the entire value.

## Using the token

Save it as the `TCDD_TOKEN` environment variable, or put it in a `.env`
file at the project root:

```
TCDD_TOKEN=<your_token_here>
```

## Notes

- **No `Bearer ` prefix.** The token is sent as a bare JWT — paste only
  the `eyJ...` value, with no prefix or surrounding quotes.
- **Refresh policy.** TCDD currently does not validate the token's
  expiration or signature, so a token captured today is likely to keep
  working for a long time. This may change without notice. If the CLI
  starts returning `TCDDAuthError` (HTTP 401/403), capture a fresh
  token using the steps above.
- **Don't share or commit your token.** It's tied to your anonymous
  session and identifies your client. `.env` is in `.gitignore` for
  this reason.
