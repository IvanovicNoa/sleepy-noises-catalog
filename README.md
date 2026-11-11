# Sleepy Noises catalog

The list of shows the **Sleepy Noises** app displays. The app downloads
`https://listen.sleepynoises.com/catalog.json` on launch and keeps its last
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
   | `artworkUrl` | no | overrides the feed artwork; must be on `megaphone.imgix.net` or this site (put the image in `images/` and use `https://listen.sleepynoises.com/images/<file>`) |
   | `featured` | no | `true` puts the show first in its row |

   A premium (ad-free) feed never goes in this file: it is public, so the feed would give the
   audio away. The schema no longer has a field for one.

3. Bump `updatedAt` and commit to `main`. The **Publish** workflow checks the file
   (`scripts/check_catalog.py`: the schema, and that no published show went missing) and publishes
   it in about a minute. If a check fails, nothing is published and the apps keep the previous
   version. What is wrong is said in a red line on the run's page (Actions → Publish), and on a
   pull request beside the file: a missing comma with its line, a field the schema refuses with
   where it is.

Categories are rows on the Home screen; `sortOrder` sets their order.

The step-by-step guide, with premium shows, is in the app's repository: `docs/adding-shows.md`.

## Take a show out for good

Phones keep what a listener saved for a show (likes, where they stopped, downloads) under the
show's `id`. So the app deletes a show only when told to by name:

1. Remove the show's entry from `podcasts`.
2. Add its `id` to `retired`:

   ```json
   "retired": ["sleepy-rain"]
   ```

3. Bump `updatedAt` and commit.

An id in `retired` stays there for good and is never used for another show. A show that is
merely missing from `podcasts` (a typo in its id, an id that was changed) is refused by the
Publish workflow, and phones would keep it anyway: the fix is to put the old id back.

The show's entries in `tags.json` can stay where they are: nothing reads them any more.

## One-time setup

Settings → Pages → Build and deployment → Source: **GitHub Actions**. Then Actions → Publish →
Run workflow (or just push a change).

Custom domain: Settings → Pages → Custom domain `listen.sleepynoises.com`, with *Enforce HTTPS*
ticked. DNS (in Squarespace): a `CNAME` record `listen` → `ivanovicnoa.github.io`; the domain is
verified in GitHub (profile → Settings → Pages). No `CNAME` file is needed because the site is
deployed by Actions. The old address `ivanovicnoa.github.io/sleepy-noises-catalog/…` redirects
here, so copies of the app from before step 21 keep working.

Check it worked: open
<https://listen.sleepynoises.com/catalog.json> in a browser.

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
  episodes that have no entry yet and opens a PR with the result. The PR's text lists every
  episode by its title with what it was tagged (the file itself names episodes by guid, shown
  beside each title). Review it, change what is wrong in `tags.json`, and merge.
  It needs two one-time settings:
  1. Settings → Secrets and variables → Actions → New repository secret
     `ANTHROPIC_API_KEY` (from console.anthropic.com → API keys). The key stays in GitHub; the
     app never sees it. A run costs cents (Message Batches API, half price).
  2. Settings → Actions → General → Workflow permissions → tick
     *Allow GitHub Actions to create and approve pull requests*.
- Pull requests opened by the workflow do not start other workflows (a GitHub rule), so the
  workflow checks `tags.json` itself before it opens one, the same way Publish does when the PR
  is merged into `main`.
- An episode whose feed gives it no `<guid>` is not tagged: the app knows it by an id of its
  own making, so an entry for it would never be found. The run names any such episode
  ("keeps the app's own guess").
- A run that goes wrong says so in yellow lines on its page: an episode Claude declined, an
  answer that could not be used, a batch that took more than five hours (it is cancelled, and
  what it had finished is kept). Those episodes are tried again the next Monday.

## The scripts and their packages

`scripts/` holds what the workflows run, with tests beside each
(`python -m unittest discover -s scripts`):

| Script | What it does | Packages |
|---|---|---|
| `check_catalog.py` | checks `catalog.json` and `tags.json` before they are published | `requirements-check.txt` |
| `check_feeds.py` | reports what the app will see of each feed; warnings only | none |
| `tag_episodes.py` | the Tag episodes workflow | `requirements-tagging.txt` |

Both package lists pin every package, also the ones the first brings along, to one version and
to the checksums of its files; `pip` then installs nothing else. This matters most for the
tagging job, which holds the API key and may write to this repository. Dependabot opens one pull
request a month to update them. Merge it when its checks are green: Publish installs the new
packages and runs the tagging script against a stand-in for the API (no key, no cost), so an
update that would break Monday's run is red here first. If it is red, leave it; the header of
each list says how to build it again. A new major version of the Anthropic SDK is not offered
(`.github/dependabot.yml`): it speaks a newer version of the API, and moving to it is done on
purpose, with Anthropic's migration guide.

## Share landing page (`listen/`)

Links shared from the app point to
`https://listen.sleepynoises.com/listen/?show=<podcast id>&episode=<feed guid>`.
The page (`listen/index.html`, `listen.js`, `listen.css`) shows the show and episode from this
catalog and the public RSS feed, tries to open the app (`sleepynoises://app/listen?…`), and offers
"Open in Sleepy Noises". Once the app is in the stores, set `APP_STORE_URL` and `PLAY_STORE_URL`
at the top of `listen/listen.js` and the "Get the app" button appears, leading to the phone's
own store.

Everything shown is inserted as text, and a strict Content-Security-Policy allows scripts only
from this site and network requests only to this site and `feeds.megaphone.fm`. The Publish
workflow copies `listen/` into the site.
