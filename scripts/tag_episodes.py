"""Tags new episodes with Claude and merges them into tags.json.

The app works out each episode's traits on the phone from its title (see
the app's lib/features/catalog/domain/episode_classifier.dart). This script
adds a reviewed layer on top: Claude reads each episode's title and
description once and picks traits from the same fixed vocabulary; the
results land in tags.json through a pull request, Noa reviews (and can edit)
them, and the app prefers them over its own guess once published.

- Only episodes without an entry in tags.json are sent, so a weekly run
  costs cents. Existing entries (including Noa's edits) are never changed.
- Requests go through the Message Batches API (half price, asynchronous).
- Structured outputs restrict the answer to the vocabulary below; anything
  else is dropped anyway.
- A refused, failed or expired request leaves the episode untagged; the app
  keeps its own guess and the next run tries again.

Usage:
    python scripts/tag_episodes.py --dry-run     # list what would be sent
    ANTHROPIC_API_KEY=... python scripts/tag_episodes.py [--max 500]

Keep TRAITS in step with the app's lib/features/catalog/domain/trait.dart
and with tags.schema.json (the Publish workflow checks the schema).
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from check_feeds import trusted

TAGS_FILE = "tags.json"
CATALOG_FILE = "catalog.json"
MAX_FEED_BYTES = 8 * 1024 * 1024
MAX_DESCRIPTION_CHARS = 1500
MAX_TRAITS = 6
FEED_UA = "SleepyNoises-Tagger/1.0"
ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
MODEL = "claude-opus-5-5"
POLL_SECONDS = 30
POLL_LIMIT_SECONDS = 5 * 60 * 60  # the job times out at 6 hours

# id -> what it means. Sounds and noise colours describe what you hear;
# story genres describe what a story is about.
TRAITS = {
    # Sounds
    "rain": "rain, drizzle, raindrops on a roof, tent or window",
    "thunder": "thunder, thunderstorms, storms",
    "ocean": "ocean, sea, waves, beach, shore",
    "water": "rivers, streams, creeks, waterfalls, lakes, fountains",
    "forest": "forests, woods, jungle, rainforest ambience",
    "birds": "birdsong, chirping birds",
    "wind": "wind, breeze, blizzard",
    "fire": "fireplace, campfire, crackling fire",
    "night": "night creatures: crickets, frogs, owls, cicadas, swamp at night",
    "city": "city ambience, traffic, streets",
    "train": "trains, railways, subway",
    "cafe": "café or coffee-shop ambience",
    "fan": "fans: box fan, ceiling fan, air conditioner",
    "airplane": "airplane cabin, jet engine hum",
    "appliance": "household machines: washing machine, dryer, vacuum, hair dryer",
    "music": "music: piano, guitar, harp, lullabies, ambient music",
    # Noise colours
    "white": "white noise",
    "pink": "pink noise",
    "brown": "brown (Brownian) noise",
    "green": "green noise",
    # Story genres
    "calm": "a calm, cosy, gentle story",
    "fantasy": "fantasy: dragons, magic, castles, kingdoms",
    "fairytale": "fairy tales",
    "nature": "a story about nature, animals, mountains",
    "history": "a story about history or the past",
    "mystery": "mystery or detective story",
    "horror": "horror, ghosts, haunted places, scary stories",
    "scifi": "science fiction, space, robots",
}

SYSTEM = f"""You tag episodes of sleep podcasts (long recordings of sounds, \
and bedtime stories) so a sleep app can recommend and search them.

For each episode, decide:
- kind: "sound" for recordings of sounds or noise, "story" for spoken stories;
- traits: what the episode *is*, chosen only from this vocabulary:
{chr(10).join(f'  - {k}: {v}' for k, v in TRAITS.items())}

Rules:
- Tag what is in this episode, mostly from its title. Descriptions often end \
with keyword lists, links or "also try our thunderstorm sounds" lines about \
other episodes: ignore those.
- Noise colours only when the episode is that noise.
- Story genres only for stories; sound traits only when you hear that sound.
- Usually one to four traits. An empty list is fine when nothing fits.
"""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["sound", "story"]},
        "traits": {
            "type": "array",
            "items": {"type": "string", "enum": list(TRAITS)},
        },
    },
    "required": ["kind", "traits"],
    "additionalProperties": False,
}


def text_of(element):
    return "".join(element.itertext()).strip() if element is not None else ""


def plain(markup):
    """HTML to plain text, whitespace collapsed."""
    no_tags = re.sub(r"<[^>]+>", " ", markup)
    return re.sub(r"\s+", " ", html.unescape(no_tags)).strip()


def fetch_feed(url):
    request = urllib.request.Request(url, headers={"User-Agent": FEED_UA})
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read(MAX_FEED_BYTES + 1)
    if len(body) > MAX_FEED_BYTES:
        raise ValueError("feed is larger than the app's size cap")
    return ET.fromstring(body)


def episodes_of(podcast):
    """(guid, title, description) for every episode the app shows: audio on
    a trusted host, a title, not a trailer, first of each guid. The guid is
    the <guid> text, or the enclosure URL without one (as in the app)."""
    root = fetch_feed(podcast["feedUrl"])
    seen = set()
    for item in root.iter("item"):
        enclosure = item.find("enclosure")
        audio = enclosure.get("url", "").strip() if enclosure is not None else ""
        if not trusted(audio):
            continue
        if text_of(item.find(f"{ITUNES}episodeType")).lower() == "trailer":
            continue
        title = text_of(item.find("title")) or text_of(item.find(f"{ITUNES}title"))
        if not title:
            continue
        guid = text_of(item.find("guid")) or audio
        if guid in seen:
            continue
        seen.add(guid)
        description = plain(
            text_of(item.find(f"{CONTENT}encoded")) or text_of(item.find("description"))
        )
        yield guid, title, description[:MAX_DESCRIPTION_CHARS]


def load_tags():
    if not os.path.exists(TAGS_FILE):
        return {"version": 1, "updatedAt": None, "episodes": []}
    with open(TAGS_FILE) as f:
        return json.load(f)


def save_tags(tags):
    tags["episodes"].sort(key=lambda e: (e["podcastId"], e["guid"]))
    tags["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(TAGS_FILE, "w") as f:
        json.dump(tags, f, indent=2, ensure_ascii=False)
        f.write("\n")


def pending_episodes(catalog, tags, limit):
    done = {(e["podcastId"], e["guid"]) for e in tags["episodes"]}
    pending = []
    for podcast in catalog["podcasts"]:
        try:
            items = list(episodes_of(podcast))
        except Exception as e:  # noqa: BLE001 - one bad feed must not stop the rest
            print(f"::warning::{podcast['id']}: feed not read ({e})")
            continue
        for guid, title, description in items:
            if (podcast["id"], guid) not in done:
                pending.append((podcast["id"], guid, title, description))
    return pending[:limit]


def tag_with_claude(pending):
    """Sends one batch and waits for it. Returns {(podcastId, guid): entry}."""
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = anthropic.Anthropic()
    by_custom_id = {f"ep-{i}": episode for i, episode in enumerate(pending)}
    requests = [
        Request(
            custom_id=custom_id,
            params=MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=4000,
                # The same system prompt for every request: cached once.
                system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
                },
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"title": title, "description": description},
                            ensure_ascii=False,
                        ),
                    }
                ],
            ),
        )
        for custom_id, (_, _, title, description) in by_custom_id.items()
    ]
    batch = client.messages.batches.create(requests=requests)
    print(f"Batch {batch.id}: {len(requests)} episodes")

    waited = 0
    while batch.processing_status != "ended":
        if waited > POLL_LIMIT_SECONDS:
            sys.exit(f"Batch {batch.id} still running after {waited}s; try again later.")
        time.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
        batch = client.messages.batches.retrieve(batch.id)
    counts = batch.request_counts
    print(f"Done: {counts.succeeded} tagged, {counts.errored} errored, {counts.expired} expired")

    tagged = {}
    for result in client.messages.batches.results(batch.id):
        podcast_id, guid, title, _ = by_custom_id[result.custom_id]
        if result.result.type != "succeeded":
            print(f"::warning::{podcast_id} / {title}: {result.result.type}")
            continue
        message = result.result.message
        if message.stop_reason != "end_turn":
            print(f"::warning::{podcast_id} / {title}: stopped ({message.stop_reason})")
            continue
        text = next((b.text for b in message.content if b.type == "text"), "")
        try:
            answer = json.loads(text)
        except json.JSONDecodeError:
            print(f"::warning::{podcast_id} / {title}: unreadable answer")
            continue
        traits = [t for t in answer.get("traits", []) if t in TRAITS]
        traits = list(dict.fromkeys(traits))[:MAX_TRAITS]
        kind = answer.get("kind")
        tagged[(podcast_id, guid)] = {
            "podcastId": podcast_id,
            "guid": guid,
            "kind": kind if kind in ("sound", "story") else "sound",
            "traits": traits,
        }
    return tagged


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="list, send nothing")
    parser.add_argument("--max", type=int, default=500, help="episodes per run")
    args = parser.parse_args()

    with open(CATALOG_FILE) as f:
        catalog = json.load(f)
    tags = load_tags()
    pending = pending_episodes(catalog, tags, args.max)
    print(f"{len(pending)} episodes without tags")
    if args.dry_run:
        for podcast_id, _, title, _ in pending:
            print(f"  {podcast_id}: {title}")
        return
    if not pending:
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set")

    tagged = tag_with_claude(pending)
    tags["episodes"].extend(tagged.values())
    save_tags(tags)
    print(f"Added {len(tagged)} entries to {TAGS_FILE}")


if __name__ == "__main__":
    main()
