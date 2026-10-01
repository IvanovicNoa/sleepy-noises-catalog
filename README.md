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

## Episode tags (`tags.json`)

The app works out what every episode is (rain, thunder, brown noise, horror…) from its title, on
the phone. `tags.json` is an optional, reviewed layer on top: an entry here wins over the app's
guess. Each entry is one episode:

```json
{ "podcastId": "sleepy-noises", "guid": "<the episode's <guid>>", "kind": "sound", "traits": ["rain", "thunder"] }
```

- Trait ids are listed in `tags.schema.json` (the same list as the app's
  `lib/features/catalog/domain/trait.dart`). Add a new trait in the app first, then here.
- Fix a wrong tag by editing the entry in a PR; the Publish workflow validates the file.
- **Tag episodes** (Actions → Tag episodes → Run workflow, and every Monday) asks Claude to tag
  episodes that have no entry yet and opens a PR with the result. Review and merge it.
  It needs two one-time settings:
  1. Settings → Secrets and variables → Actions → New repository secret
     `ANTHROPIC_API_KEY` (from console.anthropic.com → API keys). The key stays in GitHub; the
     app never sees it. A run costs cents (Message Batches API, half price).
  2. Settings → Actions → General → Workflow permissions → tick
     *Allow GitHub Actions to create and approve pull requests*.
- Pull requests opened by the workflow do not start other workflows (a GitHub rule); the Publish
  workflow validates `tags.json` when the PR is merged into `main`.

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
