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
  keeps its own guess and the next run tries again. One answer that cannot
  be used never costs the others.
- An episode the app does not know by its feed's guid (it has none, or one
  the app replaces with an id of its own) is not sent: an entry for it
  would never be found.
- A batch still running after five hours is cancelled, and what it had
  finished is kept.
- What is written always fits tags.schema.json. Nothing checks the pull
  request this opens (a GitHub rule), and a tags.json the schema refuses
  would stop the catalog from being published once it is merged.

Usage:
    python scripts/tag_episodes.py --dry-run     # list what would be sent
    ANTHROPIC_API_KEY=... python scripts/tag_episodes.py [--max 500] [--review FILE]

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
from workflow_log import one_line, warn

TAGS_FILE = "tags.json"
CATALOG_FILE = "catalog.json"
MAX_FEED_BYTES = 8 * 1024 * 1024
MAX_TITLE_CHARS = 300  # the app keeps no more of a title (RssParser)
MAX_DESCRIPTION_CHARS = 1500
MAX_TRAITS = 6
MAX_GUID_LENGTH = 200  # tags.schema.json, and the app's maxTagGuidLength
MAX_TAGGED_EPISODES = 20000  # tags.schema.json; the app reads no more
MAX_REVIEW_CHARS = 60000  # a pull request's text holds 65,536
FEED_UA = "SleepyNoises-Tagger/1.0"
ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
MODEL = "claude-opus-5-5"
POLL_SECONDS = 30
POLL_LIMIT_SECONDS = 5 * 60 * 60  # the job times out at 6 hours
CANCEL_LIMIT_SECONDS = 15 * 60  # for a cancelled batch to stop

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


def usable_guid(guid):
    """Whether the app knows an episode by its feed's guid as it is.

    Without a guid, or with one that is very long or holds a control
    character, the app gives the episode an id of its own making
    (RssParser._guid in the app): an entry under the feed's text would
    never be found. And tags.json takes no guid over 200 characters.
    """
    length = len(guid.encode("utf-16-le", "surrogatepass")) // 2  # as the app counts
    return 0 < length <= MAX_GUID_LENGTH and not re.search(r"[\x00-\x1f\x7f]", guid)


def episodes_of(podcast):
    """(guid, title, description) for every episode the app shows and knows
    by its feed's guid: audio on a trusted host, a title, not a trailer,
    first of each guid. The title as the app keeps it: one line, 300
    characters at most."""
    root = fetch_feed(podcast["feedUrl"])
    seen = set()
    for item in root.iter("item"):
        enclosure = item.find("enclosure")
        audio = enclosure.get("url", "").strip() if enclosure is not None else ""
        if not trusted(audio):
            continue
        if text_of(item.find(f"{ITUNES}episodeType")).lower() == "trailer":
            continue
        title = one_line(text_of(item.find("title")) or text_of(item.find(f"{ITUNES}title")))
        title = title[:MAX_TITLE_CHARS]
        if not title:
            continue
        guid = text_of(item.find("guid"))
        if not usable_guid(guid):
            print(f"  {podcast['id']}: \"{title}\" keeps the app's own guess (no guid the app knows it by)")
            continue
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
    with open(TAGS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_tags(tags):
    tags["episodes"].sort(key=lambda e: (e["podcastId"], e["guid"]))
    tags["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(TAGS_FILE, "w", encoding="utf-8") as f:
        json.dump(tags, f, indent=2, ensure_ascii=False)
        f.write("\n")


def pending_episodes(catalog, tags, limit):
    done = {(e["podcastId"], e["guid"]) for e in tags["episodes"]}
    pending = []
    for podcast in catalog["podcasts"]:
        try:
            items = list(episodes_of(podcast))
        except Exception as e:  # noqa: BLE001 - one bad feed must not stop the rest
            warn(f"{podcast['id']}: feed not read ({e})")
            continue
        for guid, title, description in items:
            if (podcast["id"], guid) not in done:
                pending.append((podcast["id"], guid, title, description))
    return pending[:limit]


def wait_for(client, batch):
    """The batch, once it has ended.

    One still running after POLL_LIMIT_SECONDS is cancelled. Left alone it
    would finish with nobody to read it, and the next run would send the
    same episodes again. What it had finished by then still counts.
    """
    waited = 0
    cancelled = False
    while batch.processing_status != "ended":
        if waited >= POLL_LIMIT_SECONDS and not cancelled:
            warn(f"Batch {batch.id} still running after {waited}s: cancelling what is left of it")
            client.messages.batches.cancel(batch.id)
            cancelled = True
        if waited >= POLL_LIMIT_SECONDS + CANCEL_LIMIT_SECONDS:
            sys.exit(f"Batch {batch.id} was cancelled and has not stopped; try again later.")
        time.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
        batch = client.messages.batches.retrieve(batch.id)
    return batch


def reviewed(message):
    """(kind, traits) from one answer; a ValueError says why there is none.

    Structured outputs keep an answer to the vocabulary. It is checked here
    all the same: whatever comes back, tags.json has to stay a file the
    schema accepts. A kind that is neither "sound" nor "story" is left out
    (None), and the app decides for itself.
    """
    if message.stop_reason != "end_turn":  # a refusal, or out of tokens
        raise ValueError(f"stopped ({message.stop_reason})")
    text = next((b.text for b in message.content if b.type == "text"), "")
    try:
        answer = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError("unreadable answer") from None
    if not isinstance(answer, dict) or not isinstance(answer.get("traits"), list):
        raise ValueError("an answer of another shape")
    traits = [t for t in answer["traits"] if isinstance(t, str) and t in TRAITS]
    kind = answer.get("kind")
    return (kind if kind in ("sound", "story") else None), list(dict.fromkeys(traits))[:MAX_TRAITS]


def collect(results, by_custom_id):
    """{(podcastId, guid): entry} for every result with a usable answer.

    The batch is paid for by now. A result that cannot be used, or the list
    of results breaking off, is a warning and no more: the episodes read
    until then are kept, and the next run sends the others again.
    """
    tagged = {}
    try:
        for result in results:
            episode = by_custom_id.get(result.custom_id)
            if episode is None:
                continue
            podcast_id, guid, title, _ = episode
            try:
                if result.result.type != "succeeded":  # errored, canceled, expired
                    raise ValueError(result.result.type)
                kind, traits = reviewed(result.result.message)
            except Exception as e:  # noqa: BLE001 - one odd result must not lose the rest
                why = e if isinstance(e, ValueError) else type(e).__name__
                warn(f"{podcast_id} / {title}: {why}")
                continue
            entry = {"podcastId": podcast_id, "guid": guid}
            if kind:
                entry["kind"] = kind
            entry["traits"] = traits
            tagged[(podcast_id, guid)] = entry
    except Exception as e:  # noqa: BLE001 - the connection, while the results are read
        warn(
            f"the results broke off ({type(e).__name__}): {len(tagged)} of "
            f"{len(by_custom_id)} episodes read, the others wait for the next run"
        )
    return tagged


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

    # The wait is hours of small requests, and one of them failing for good
    # ends the run with the batch still going: more tries than the usual two.
    patient = client.with_options(max_retries=6)
    batch = wait_for(patient, batch)
    counts = batch.request_counts
    print(
        f"Done: {counts.succeeded} tagged, {counts.errored} errored, "
        f"{counts.canceled} canceled, {counts.expired} expired"
    )
    return collect(patient.messages.batches.results(batch.id), by_custom_id)


def review_page(pending, tagged):
    """The pull request's text: every tagged episode by its title.

    tags.json names an episode by its guid, which tells a reader nothing.
    This lists title, answer and guid, so each can be checked and then
    found in the file. Titles come from feeds: they go in a code block,
    where nothing in them is markup or a mention.
    """
    lines = [
        "Claude tagged the episodes below with traits from the app's vocabulary. Check them,",
        "change or remove what is wrong in `tags.json` (find an entry by its guid), and merge.",
        "The app picks them up after the next publish.",
        "",
        "```",
    ]
    size = sum(len(line) + 1 for line in lines)
    left_out = 0
    no_backquotes = str.maketrans("`", "'")  # three in a row would end the code block
    for podcast_id, guid, title, _ in pending:
        entry = tagged.get((podcast_id, guid))
        if entry is None:
            continue
        answer = ", ".join(entry["traits"]) or "no traits"
        if "kind" in entry:
            answer = f"{entry['kind']}: {answer}"
        row = (
            f"{title.translate(no_backquotes)}\n"
            f"    {answer}    [{podcast_id} / {guid.translate(no_backquotes)}]"
        )
        if size + len(row) + 1 > MAX_REVIEW_CHARS:
            left_out += 1
            continue
        lines.append(row)
        size += len(row) + 1
    lines.append("```")
    if left_out:
        lines += ["", f"And {left_out} more, in the changes to `tags.json` only."]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="list, send nothing")
    parser.add_argument("--max", type=int, default=500, help="episodes per run")
    parser.add_argument("--review", metavar="FILE", help="write the pull request's text here")
    args = parser.parse_args()

    with open(CATALOG_FILE, encoding="utf-8") as f:
        catalog = json.load(f)
    tags = load_tags()
    room = max(0, MAX_TAGGED_EPISODES - len(tags["episodes"]))
    if room < args.max:
        warn(f"{TAGS_FILE} has room for {room} more episodes (it holds {MAX_TAGGED_EPISODES} at most)")
    pending = pending_episodes(catalog, tags, max(0, min(args.max, room)))
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
    if args.review:
        with open(args.review, "w", encoding="utf-8") as f:
            f.write(review_page(pending, tagged))


if __name__ == "__main__":
    main()
