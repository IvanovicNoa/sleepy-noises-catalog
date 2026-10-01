"""Which Megaphone server does each client get sent to?

Megaphone redirects traffic.megaphone.fm to different CDNs (dcs-cached,
dcs-spotify, ...). This asks for the same episodes as a desktop browser,
as the iPhone's audio player and as the Sleepy Noises app, and reports
where each lands and whether audio arrives. Run by hand from Actions.
"""

import json
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

EPISODES = {
    "newest Sleepy Noises (fails for Noa)": "https://pdst.fm/e/traffic.megaphone.fm/EESSI8000409927.mp3",
    "Wall Clock 2024 (plays on Noa's Mac)": "https://pdst.fm/e/traffic.megaphone.fm/EESSI1289073184.mp3?updated=1711211065",
    "newest White Noise": "https://traffic.megaphone.fm/EESSI3454705070.mp3",
}
CLIENTS = {
    "Mac Chrome": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "iPhone player": "AppleCoreMedia/1.0.0.23G83 (iPhone; U; CPU OS 26_6_1 like Mac OS X; en_us)",
    "iPhone Safari": "Mozilla/5.0 (iPhone; CPU iPhone OS 26_6_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Mobile/15E148 Safari/604.1",
    "Sleepy Noises app": "SleepyNoises/0.1.0",
    "Apple Podcasts": "Podcasts/1.1.0 CFNetwork/3826.500.111 Darwin/24.4.0",
}


class Hops(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        self.hops = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.hops.append(urlsplit(newurl).hostname)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def probe(url, ua):
    hops = Hops()
    opener = urllib.request.build_opener(hops)
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Range": "bytes=0-"})
    began = time.monotonic()
    try:
        with opener.open(req, timeout=30) as r:
            body = r.read(512 * 1024)
            return {"route": hops.hops, "status": r.status, "bytes": len(body),
                    "audio": body[:3] == b"ID3" or body[:1] == b"\xff",
                    "s": round(time.monotonic() - began, 1)}
    except urllib.error.HTTPError as e:
        return {"route": hops.hops, "status": e.code, "page": e.read(200).decode("latin-1")}
    except Exception as e:  # noqa: BLE001
        return {"route": hops.hops, "error": repr(e)}


for name, url in EPISODES.items():
    print(f"\n=== {name}")
    for client, ua in CLIENTS.items():
        print(f"  {client:18} {json.dumps(probe(url, ua))}")
sys.exit(0)
