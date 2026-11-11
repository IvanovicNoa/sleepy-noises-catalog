"""Tests for check_feeds.py: python -m unittest discover -s scripts

No network: the feed and the audio server are stood in for.
"""

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

import check_feeds
from check_feeds import trusted


def printed(function, *arguments):
    out = io.StringIO()
    with redirect_stdout(out):
        function(*arguments)
    return out.getvalue()


class Trusted(unittest.TestCase):
    def test_the_apps_hosts_over_https_only(self):
        for url in (
            "https://traffic.megaphone.fm/A.mp3",
            "https://megaphone.fm/A.mp3",
            "https://dcs.megaphone.fm/A.mp3?x=1",
            "https://pdst.fm/e/traffic.megaphone.fm/A.mp3",
            "https://LISTEN.sleepynoises.com/a.png",
        ):
            self.assertTrue(trusted(url), url)
        for url in (
            "http://traffic.megaphone.fm/A.mp3",
            "https://megaphone.fm.evil.example/A.mp3",
            "https://evilmegaphone.fm/A.mp3",
            "https://megaphone.fm@evil.example/A.mp3",
            "https://evil.example/megaphone.fm/A.mp3",
            "//traffic.megaphone.fm/A.mp3",
            "",
        ):
            self.assertFalse(trusted(url), url)


FEED = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>
<title>Sleepy
::error::Noises</title>
<item><title>Rain
::error::On A Tent</title>
<itunes:duration>10
::error::hours</itunes:duration>
<enclosure url="https://traffic.megaphone.fm/A.mp3?x=&#10;::error::address" length="1&#10;::error::length" type="audio/mpeg&#10;::error::type"/>
</item></channel></rss>"""


class FakeResponse:
    status = 200
    headers = {"Content-Type": "application/rss+xml"}

    def read(self, limit):
        return FEED

    def __enter__(self):
        return self

    def __exit__(self, *details):
        return False


class Check(unittest.TestCase):
    def test_nothing_a_feed_says_starts_a_line(self):
        # A line that starts with "::" is a command to the workflow.
        probe = {"status": 206, "content_range": "bytes 0-1/x", "hops": []}
        with (
            mock.patch.object(check_feeds.urllib.request, "urlopen", return_value=FakeResponse()),
            mock.patch.object(check_feeds, "range_probe", return_value=probe),
        ):
            said = printed(check_feeds.check, {"id": "sleepy-noises", "feedUrl": "https://feeds.megaphone.fm/TEST"})
        lines = said.splitlines()
        self.assertIn("episodes: 1 in feed, 1 usable by the app", lines)
        self.assertIn("  enclosure: https://traffic.megaphone.fm/A.mp3?x= ::error::address", lines)
        self.assertEqual([line for line in lines if line.lstrip().startswith("::")], [])


if __name__ == "__main__":
    unittest.main()
