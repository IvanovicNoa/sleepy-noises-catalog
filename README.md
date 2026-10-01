# Sleepy Noises catalog

The list of shows the **Sleepy Noises** app displays. The app downloads
`https://ivanovicnoa.github.io/sleepy-noises-catalog/catalog.json` on launch and keeps its last
good copy for offline use. Change this file and every installed app picks it up, no app update
needed.

This repository is **public on purpose** (GitHub Pages is free for public repos). It must never
contain anything secret: no Megaphone API token, no private or premium feed URLs.

## Add or change a show

1. In Megaphone open the podcast, click **Feed**, copy the URL
   (it looks like `https://feeds.megaphone.fm/ABC1234567890`).
2. Edit `catalog.json` (the pencil icon on GitHub is fine) and add an entry to `podcasts`:

   ```json
   { "id": "sleepy-rain", "feedUrl": "https://feeds.megaphone.fm/ABC1234567890", "categoryId": "nature" }
   ```

   | Field | Required | Notes |
   |---|---|---|
   | `id` | yes | lowercase slug; **never change it once published** (likes, downloads and progress on phones are keyed by it) |
   | `feedUrl` | yes | must be `https://feeds.megaphone.fm/...` |
   | `categoryId` | yes | one of the `id`s in `categories` |
   | `title` | no | overrides the title from the RSS feed |
   | `artworkUrl` | no | overrides the feed artwork; must be on `megaphone.imgix.net` or this site (put the image in `images/` and use `https://ivanovicnoa.github.io/sleepy-noises-catalog/images/<file>`) |
   | `featured` | no | `true` puts the show first in its row |
   | `premiumFeedUrl` | no | **never use.** This file is public, so a premium feed here would give the ad-free audio away. Premium feeds will come from the authenticated backend (see the app's `docs/roadmap.md` §6.3); the field will be removed from the schema then. |

3. Bump `updatedAt` and commit to `main`. The **Publish** workflow validates the file against
   `catalog.schema.json` and publishes it in about a minute. If validation fails, nothing is
   published and the apps keep the previous version.

Categories are rows on the Home screen; `sortOrder` sets their order.

## One-time setup

Settings → Pages → Build and deployment → Source: **GitHub Actions**. Then Actions → Publish →
Run workflow (or just push a change).

Check it worked: open
<https://ivanovicnoa.github.io/sleepy-noises-catalog/catalog.json> in a browser.

## Share landing page (`listen/`)

Links shared from the app point to
`https://ivanovicnoa.github.io/sleepy-noises-catalog/listen/?show=<podcast id>&episode=<feed guid>`.
The page (`listen/index.html`, `listen.js`, `listen.css`) shows the show and episode from this
catalog and the public RSS feed, tries to open the app (`sleepynoises://app/listen?…`), and offers
"Open in Sleepy Noises". Once the app is in the App Store, set `APP_STORE_URL` at the top of
`listen/listen.js` and the "Get the app" button appears.

Everything shown is inserted as text, and a strict Content-Security-Policy allows scripts only
from this site and network requests only to this site and `feeds.megaphone.fm`. The Publish
workflow copies `listen/` into the site.
