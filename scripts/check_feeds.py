"""Health check for every feed in catalog.json.

Reports what the app will see for each show, as GitHub Actions warnings,
without ever failing the workflow (a feed being down for a minute must not
block publishing the catalog):

- the feed downloads, is well-formed RSS, and fits the app's size cap;
- how many episodes it has and on which hosts their audio files live
  (the app skips episodes whose audio is not on a trusted host);
- for the newest episode: the redirect chain, and whether the server
  answers byte-range requests at the start and in the middle of the file,
  which the iPhone and Android players need to stream and seek.

Keep TRUSTED_HOSTS and MAX_FEED_BYTES in step with the app's
lib/core/config/app_config.dart.
"""

import json
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from urllib.parse import urlsplit

TRUSTED_HOSTS = {
    "megaphone.fm",
    "megaphone.cloud",
    "megaphone.imgix.net",
    "ivanovicnoa.github.io",
    "pdst.fm",  # Spotify Ad Analytics prefix; redirects to traffic.megaphone.fm
}
MAX_FEED_BYTES = 8 * 1024 * 1024
ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
# The iPhone's player (AVPlayer) identifies itself like this; Megaphone may
# answer differently per client, so test as the phone does.
PLAYER_UA = "AppleCoreMedia/1.0.0.23G83 (iPhone; U; CPU OS 26_6_1 like Mac OS X; en_us)"
FEED_UA = "SleepyNoises-FeedCheck/1.0"


def warn(message):
    print(f"::warning::{message}")


def trusted(url):
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(
        host == h or host.endswith("." + h) for h in TRUSTED_HOSTS
    )


class RecordingRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        self.hops = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.hops.append((code, urlsplit(req.full_url).hostname))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def range_probe(url, start):
    """Requests two bytes at `start`; returns a dict describing the answer."""
    recorder = RecordingRedirects()
    opener = urllib.request.build_opener(recorder)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": PLAYER_UA, "Range": f"bytes={start}-{start + 1}"},
    )
    began = time.monotonic()
    try:
        with opener.open(request, timeout=30) as response:
            first = response.read(2)
            status = response.status
            headers = response.headers
            final_host = urlsplit(response.url).hostname
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "hops": recorder.hops}
    except Exception as e:  # noqa: BLE001 - report anything
        return {"error": repr(e), "hops": recorder.hops}
    return {
        "status": status,
        "seconds": round(time.monotonic() - began, 2),
        "hops": recorder.hops,
        "final_host": final_host,
        "accept_ranges": headers.get("Accept-Ranges"),
        "content_range": headers.get("Content-Range"),
        "content_type": headers.get("Content-Type"),
        "content_length": headers.get("Content-Length"),
        "bytes_read": len(first),
    }


def check(podcast):
    pid, url = podcast["id"], podcast["feedUrl"]
    print(f"\n=== {pid}  {url}")
    request = urllib.request.Request(url, headers={"User-Agent": FEED_UA})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(MAX_FEED_BYTES * 4)
            print(
                f"feed: HTTP {response.status}, {len(body):,} bytes, "
                f"type {response.headers.get('Content-Type')}, "
                f"etag {'yes' if response.headers.get('ETag') else 'no'}"
            )
    except Exception as e:  # noqa: BLE001
        warn(f"{pid}: feed could not be downloaded: {e!r}")
        return
    if len(body) > MAX_FEED_BYTES:
        warn(f"{pid}: feed is {len(body):,} bytes, over the app's {MAX_FEED_BYTES:,} cap")

    try:
        body.decode("utf-8")
    except UnicodeDecodeError:
        warn(f"{pid}: feed is not valid UTF-8; the app rejects it")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        warn(f"{pid}: feed is not well-formed XML: {e}")
        return
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        warn(f"{pid}: not an RSS feed (root <{root.tag}>)")
        return
    print(f"title: {channel.findtext('title')!r}")

    items = channel.findall("item")
    hosts = Counter()
    usable = []
    for item in items:
        enclosure = item.find("enclosure")
        audio = enclosure.get("url", "") if enclosure is not None else ""
        hosts[urlsplit(audio).hostname or "(none)"] += 1
        if trusted(audio):
            usable.append((item, enclosure, audio))
    print(f"episodes: {len(items)} in feed, {len(usable)} usable by the app")
    print(f"audio hosts: {dict(hosts)}")
    untrusted = {h: n for h, n in hosts.items() if not trusted(f"https://{h}/")}
    if untrusted:
        warn(f"{pid}: episodes on hosts the app does not trust: {untrusted}")
    if items and not usable:
        warn(f"{pid}: the app will show NO episodes for this show")

    sample = usable[0] if usable else None
    if sample is None and items:
        enclosure = items[0].find("enclosure")
        if enclosure is not None:
            sample = (items[0], enclosure, enclosure.get("url", ""))
    if sample is None:
        return
    item, enclosure, audio = sample
    print(f"newest episode: {item.findtext('title')!r}")
    print(f"  enclosure: {audio}")
    print(
        f"  declared length {enclosure.get('length')} bytes, type {enclosure.get('type')}, "
        f"itunes:duration {item.findtext(ITUNES + 'duration')}"
    )
    start = range_probe(audio, 0)
    print(f"  range at start: {json.dumps(start)}")
    if start.get("status") != 206:
        warn(f"{pid}: audio server did not answer a range request with 206: {start}")
    total = None
    if start.get("content_range") and "/" in start["content_range"]:
        tail = start["content_range"].rsplit("/", 1)[1]
        total = int(tail) if tail.isdigit() else None
    if total:
        middle = range_probe(audio, total // 2)
        print(f"  range in the middle: {json.dumps(middle)}")
        if middle.get("status") != 206:
            warn(f"{pid}: seeking into the middle of the file is not supported: {middle}")


def main():
    catalog = json.load(open("catalog.json"))
    for podcast in catalog["podcasts"]:
        if "REPLACE-WITH" in podcast["feedUrl"]:
            continue
        check(podcast)
    return 0


if __name__ == "__main__":
    sys.exit(main())
