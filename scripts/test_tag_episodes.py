"""Tests for tag_episodes.py: python -m unittest discover -s scripts

They need neither the Anthropic SDK nor a network. A batch and its results
are stood in for by plain objects with the fields the script reads
(processing_status; custom_id, result.type, result.message.stop_reason,
result.message.content[].type and .text).
"""

import io
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from types import SimpleNamespace as Obj
from unittest import mock

import jsonschema

import tag_episodes
from tag_episodes import (
    MAX_GUID_LENGTH,
    MAX_REVIEW_CHARS,
    MAX_TAGGED_EPISODES,
    MAX_TRAITS,
    OUTPUT_SCHEMA,
    TRAITS,
    collect,
    episodes_of,
    pending_episodes,
    review_page,
    reviewed,
    usable_guid,
    wait_for,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOW = {"id": "sleepy-noises", "feedUrl": "https://feeds.megaphone.fm/TEST"}
AUDIO = "https://traffic.megaphone.fm/A.mp3"


def schema():
    with open(os.path.join(ROOT, "tags.schema.json"), encoding="utf-8") as file:
        return json.load(file)


def item(title="Rain On A Tent", guid="guid-1", audio=AUDIO, more=""):
    guid = f"<guid>{guid}</guid>" if guid is not None else ""
    return (
        f"<item><title>{title}</title>{guid}"
        "<description>&lt;p&gt;Soft &lt;b&gt;rain&lt;/b&gt;&amp;nbsp;all night.&lt;/p&gt;</description>"
        f'<enclosure url="{audio}"/>{more}</item>'
    )


def feed(*items):
    return ET.fromstring(
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        f"<channel><title>Sleepy Noises</title>{''.join(items)}</channel></rss>"
    )


def episodes(*items):
    """What episodes_of makes of a feed, and what it printed."""
    out = io.StringIO()
    with mock.patch.object(tag_episodes, "fetch_feed", return_value=feed(*items)), redirect_stdout(out):
        found = list(episodes_of(SHOW))
    return found, out.getvalue()


def answer(text, stop_reason="end_turn"):
    return Obj(stop_reason=stop_reason, content=[Obj(type="text", text=text)])


def result(custom_id, text='{"kind": "sound", "traits": ["rain"]}', kind="succeeded", stop_reason="end_turn"):
    message = answer(text, stop_reason) if kind == "succeeded" else None
    return Obj(custom_id=custom_id, result=Obj(type=kind, message=message))


def collected(results, pending):
    """What collect makes of a batch's results, and what it printed."""
    out = io.StringIO()
    with redirect_stdout(out):
        tagged = collect(results, {f"ep-{i}": episode for i, episode in enumerate(pending)})
    return tagged, out.getvalue()


def pending_list(count):
    return [("sleepy-noises", f"guid-{i}", f"Rain {i}", "") for i in range(count)]


class Episodes(unittest.TestCase):
    def test_the_episodes_the_app_shows(self):
        found, _ = episodes(
            item(),
            item(title="Elsewhere", guid="guid-2", audio="https://evil.example/A.mp3"),
            item(title="Not Https", guid="guid-3", audio="http://traffic.megaphone.fm/A.mp3"),
            item(title="Coming Soon", guid="guid-4", more="<itunes:episodeType>Trailer</itunes:episodeType>"),
            item(title="", guid="guid-5"),
            item(title="The Same Again", guid="guid-1"),
            item(title="Ocean", guid="guid-6"),
        )
        self.assertEqual(
            found,
            [
                ("guid-1", "Rain On A Tent", "Soft rain all night."),
                ("guid-6", "Ocean", "Soft rain all night."),
            ],
        )

    def test_an_episode_without_a_guid_is_not_sent(self):
        # The app knows it by an id of its own making, never by its address:
        # an entry under the address would not be found, and would put an
        # address into tags.json.
        found, said = episodes(item(title="No Guid", guid=None), item(title="Blank", guid="  "))
        self.assertEqual(found, [])
        self.assertIn('"No Guid" keeps the app\'s own guess', said)
        self.assertNotIn("megaphone.fm", said)

    def test_a_guid_the_app_replaces_is_not_sent(self):
        found, _ = episodes(
            item(title="Fits", guid="g" * MAX_GUID_LENGTH),
            item(title="Too Long", guid="g" * (MAX_GUID_LENGTH + 1)),
            item(title="A Tab Inside", guid="a&#9;b"),
        )
        self.assertEqual([title for _, title, _ in found], ["Fits"])

    def test_a_guid_is_measured_as_the_app_measures_it(self):
        self.assertTrue(usable_guid("0a1b2c3d-1111-2222-3333-444455556666"))
        self.assertFalse(usable_guid(""))
        moon = "\N{CRESCENT MOON}"  # two units to the app, one character here
        self.assertTrue(usable_guid(moon * (MAX_GUID_LENGTH // 2)))
        self.assertFalse(usable_guid(moon * (MAX_GUID_LENGTH // 2 + 1)))
        self.assertFalse(usable_guid("line\nbreak"))
        self.assertFalse(usable_guid("del\x7f"))

    def test_a_title_is_one_line_of_300_characters(self):
        found, _ = episodes(
            item(title="Two&#10;::error::Lines\t here", guid="guid-1"),
            item(title="T" * 5000, guid="guid-2"),
        )
        self.assertEqual(found[0][1], "Two ::error::Lines here")
        self.assertEqual(found[1][1], "T" * 300)

    def test_a_description_is_plain_text_of_1500_characters(self):
        long = item(guid="guid-1").replace("Soft", "Soft " + "z" * 5000)
        found, _ = episodes(long)
        self.assertEqual(len(found[0][2]), 1500)
        self.assertNotIn("<", found[0][2])


class Pending(unittest.TestCase):
    CATALOG = {
        "podcasts": [
            {"id": "broken", "feedUrl": "https://feeds.megaphone.fm/B"},
            SHOW,
        ]
    }

    def pending(self, tags, limit=500):
        def fetch(url):
            if url.endswith("/B"):
                raise ET.ParseError("not\n::error::well-formed")
            return feed(*(item(title=f"Rain {i}", guid=f"guid-{i}") for i in range(4)))

        out = io.StringIO()
        with mock.patch.object(tag_episodes, "fetch_feed", side_effect=fetch), redirect_stdout(out):
            found = pending_episodes(self.CATALOG, {"episodes": tags}, limit)
        return found, out.getvalue()

    def test_only_episodes_without_an_entry(self):
        done = [
            {"podcastId": "sleepy-noises", "guid": "guid-1", "traits": []},
            {"podcastId": "another-show", "guid": "guid-2", "traits": ["rain"]},
        ]
        found, _ = self.pending(done)
        self.assertEqual([guid for _, guid, _, _ in found], ["guid-0", "guid-2", "guid-3"])

    def test_no_more_than_asked_for(self):
        found, _ = self.pending([], limit=2)
        self.assertEqual(len(found), 2)

    def test_one_feed_that_cannot_be_read_does_not_stop_the_others(self):
        found, said = self.pending([])
        self.assertEqual(len(found), 4)
        # Said as one warning, whatever the feed's error holds.
        self.assertEqual(said.splitlines(), ["::warning::broken: feed not read (not ::error::well-formed)"])


class Answers(unittest.TestCase):
    def test_a_good_answer(self):
        self.assertEqual(
            reviewed(answer('{"kind": "story", "traits": ["calm", "nature"]}')),
            ("story", ["calm", "nature"]),
        )
        self.assertEqual(reviewed(answer('{"kind": "sound", "traits": []}')), ("sound", []))

    def test_only_the_vocabulary_once_each_six_at_most(self):
        traits = ["rain", {"a": 1}, "nope", 7, None, "rain", ["rain"], "ocean"]
        self.assertEqual(
            reviewed(answer(json.dumps({"kind": "sound", "traits": traits}))),
            ("sound", ["rain", "ocean"]),
        )
        many = json.dumps({"kind": "sound", "traits": list(TRAITS)})
        self.assertEqual(reviewed(answer(many))[1], list(TRAITS)[:MAX_TRAITS])

    def test_a_kind_that_is_neither_is_left_to_the_app(self):
        for kind in ('"music"', "null", '["sound"]', "7"):
            self.assertEqual(reviewed(answer(f'{{"kind": {kind}, "traits": ["rain"]}}')), (None, ["rain"]))
        self.assertEqual(reviewed(answer('{"traits": ["rain"]}')), (None, ["rain"]))

    def test_answers_that_cannot_be_used_say_why(self):
        cases = {
            "stopped (refusal)": answer("", "refusal"),
            "stopped (max_tokens)": answer('{"kind": "sound", "tra', "max_tokens"),
            "unreadable answer": answer("Sure! Here are the tags."),
            "an answer of another shape": answer("null"),
        }
        for why, message in cases.items():
            with self.assertRaises(ValueError) as raised:
                reviewed(message)
            self.assertEqual(str(raised.exception), why)
        for text in ('["rain"]', '"rain"', "7", '{"kind": "sound"}', '{"kind": "sound", "traits": "rain"}'):
            with self.assertRaises(ValueError):
                reviewed(answer(text))
        # Nothing to read at all (no text block).
        with self.assertRaises(ValueError):
            reviewed(Obj(stop_reason="end_turn", content=[]))

    def test_one_odd_result_does_not_lose_the_others(self):
        # A batch is paid for when its results are read. This one used to
        # end with nothing written: "null" has no .get.
        tagged, said = collected(
            [
                result("ep-0", "null"),
                result("ep-1", stop_reason="refusal"),
                result("ep-2", kind="errored"),
                result("ep-3", kind="canceled"),
                result("ep-4", kind="expired"),
                result("ep-5", '{"kind": "story", "traits": ["horror"]}'),
                result("ep-6"),
                result("ep-99"),  # not one of ours
                Obj(custom_id="ep-7", result=Obj(type="succeeded", message=None)),  # no message at all
            ],
            pending_list(8),
        )
        self.assertEqual(
            list(tagged.values()),
            [
                {"podcastId": "sleepy-noises", "guid": "guid-5", "kind": "story", "traits": ["horror"]},
                {"podcastId": "sleepy-noises", "guid": "guid-6", "kind": "sound", "traits": ["rain"]},
            ],
        )
        self.assertEqual(
            said.splitlines(),
            [
                "::warning::sleepy-noises / Rain 0: an answer of another shape",
                "::warning::sleepy-noises / Rain 1: stopped (refusal)",
                "::warning::sleepy-noises / Rain 2: errored",
                "::warning::sleepy-noises / Rain 3: canceled",
                "::warning::sleepy-noises / Rain 4: expired",
                "::warning::sleepy-noises / Rain 7: AttributeError",
            ],
        )

    def test_results_that_break_off_keep_what_was_read(self):
        def results():
            yield result("ep-0")
            yield result("ep-1")
            raise ConnectionError("peer closed connection")

        tagged, said = collected(results(), pending_list(5))
        self.assertEqual(sorted(guid for _, guid in tagged), ["guid-0", "guid-1"])
        self.assertEqual(
            said.splitlines(),
            [
                "::warning::the results broke off (ConnectionError): 2 of 5 episodes read, "
                "the others wait for the next run"
            ],
        )

    def test_whatever_comes_back_the_file_fits_its_schema(self):
        odd = [
            '{"kind": "sound", "traits": ["rain", "rain", "thunder"]}',
            '{"kind": "noise", "traits": ' + json.dumps(list(TRAITS) * 2) + "}",
            '{"traits": []}',
            '{"kind": null, "traits": [1, {"x": []}, "ocean"]}',
        ]
        pending = [("sleepy-noises", "g" * MAX_GUID_LENGTH, "Rain", "")] + pending_list(len(odd))[1:]
        tagged, _ = collected([result(f"ep-{i}", text) for i, text in enumerate(odd)], pending)
        self.assertEqual(len(tagged), len(odd))
        tags = {"version": 1, "updatedAt": "2026-11-11T12:00:00Z", "episodes": list(tagged.values())}
        jsonschema.validate(tags, schema(), format_checker=jsonschema.FormatChecker())


class Waiting(unittest.TestCase):
    """wait_for against a batch that ends after `ends_after` polls, or, when
    it never would, some polls after it is cancelled."""

    def wait(self, ends_after=None, stops_after_cancel=2):
        calls = []
        state = {"polls": 0, "cancelled_at": None}

        def status():
            if ends_after is not None and state["polls"] >= ends_after:
                return "ended"
            cancelled_at = state["cancelled_at"]
            if cancelled_at is not None and stops_after_cancel is not None:
                if state["polls"] >= cancelled_at + stops_after_cancel:
                    return "ended"
            return "in_progress" if cancelled_at is None else "canceling"

        def retrieve(batch_id):
            state["polls"] += 1
            calls.append("retrieve")
            return Obj(id=batch_id, processing_status=status())

        def cancel(batch_id):
            state["cancelled_at"] = state["polls"]
            calls.append("cancel")
            return Obj(id=batch_id, processing_status="canceling")

        client = Obj(messages=Obj(batches=Obj(retrieve=retrieve, cancel=cancel)))
        out = io.StringIO()
        with (
            mock.patch.object(tag_episodes, "POLL_SECONDS", 30),
            mock.patch.object(tag_episodes, "POLL_LIMIT_SECONDS", 300),
            mock.patch.object(tag_episodes, "CANCEL_LIMIT_SECONDS", 120),
            mock.patch.object(tag_episodes.time, "sleep") as sleep,
            redirect_stdout(out),
        ):
            try:
                ended = wait_for(client, Obj(id="batch-1", processing_status=status()))
            except SystemExit as stop:
                ended = stop
        slept = [call.args[0] for call in sleep.call_args_list]
        return ended, calls, slept, out.getvalue()

    def test_a_batch_that_has_ended_is_not_waited_for(self):
        ended, calls, slept, _ = self.wait(ends_after=0)
        self.assertEqual(ended.processing_status, "ended")
        self.assertEqual((calls, slept), ([], []))

    def test_asks_every_half_minute_until_it_has_ended(self):
        ended, calls, slept, said = self.wait(ends_after=3)
        self.assertEqual(ended.processing_status, "ended")
        self.assertEqual(calls, ["retrieve"] * 3)
        self.assertEqual(slept, [30, 30, 30])
        self.assertEqual(said, "")

    def test_a_batch_that_runs_too_long_is_cancelled_once_and_then_read(self):
        # It used to be left running: finished unseen, and sent again by the
        # next run.
        ended, calls, slept, said = self.wait(ends_after=None)
        self.assertEqual(ended.processing_status, "ended")
        self.assertEqual(calls, ["retrieve"] * 10 + ["cancel"] + ["retrieve"] * 2)
        self.assertEqual(
            said.splitlines(),
            ["::warning::Batch batch-1 still running after 300s: cancelling what is left of it"],
        )

    def test_gives_up_on_a_cancelled_batch_that_does_not_stop(self):
        ended, calls, _, _ = self.wait(ends_after=None, stops_after_cancel=None)
        self.assertIsInstance(ended, SystemExit)
        self.assertIn("was cancelled and has not stopped", str(ended))
        self.assertEqual(calls.count("cancel"), 1)
        self.assertEqual(calls.count("retrieve"), 14)  # 300 s, then 120 s more


class Vocabulary(unittest.TestCase):
    """The script, the schema and so the app name the same things."""

    def test_the_traits_are_the_schemas(self):
        self.assertEqual(list(TRAITS), schema()["$defs"]["trait"]["enum"])
        self.assertEqual(OUTPUT_SCHEMA["properties"]["traits"]["items"]["enum"], list(TRAITS))

    def test_the_limits_are_the_schemas(self):
        episodes = schema()["properties"]["episodes"]
        entry = episodes["items"]["properties"]
        self.assertEqual(MAX_GUID_LENGTH, entry["guid"]["maxLength"])
        self.assertEqual(MAX_TAGGED_EPISODES, episodes["maxItems"])
        self.assertLessEqual(MAX_TRAITS, entry["traits"]["maxItems"])
        self.assertEqual(entry["kind"]["enum"], OUTPUT_SCHEMA["properties"]["kind"]["enum"])


class Review(unittest.TestCase):
    def test_every_tagged_episode_by_its_title(self):
        pending = [
            ("sleepy-noises", "guid-1", "Rain On A Tent", ""),
            ("sleepy-noises", "guid-2", "Never Answered", ""),
            ("terrified", "guid-3", "The House", ""),
            ("terrified", "guid-4", "Nothing Fits", ""),
        ]
        tagged = {
            ("sleepy-noises", "guid-1"): {"podcastId": "sleepy-noises", "guid": "guid-1", "kind": "sound", "traits": ["rain", "wind"]},
            ("terrified", "guid-3"): {"podcastId": "terrified", "guid": "guid-3", "traits": ["horror"]},
            ("terrified", "guid-4"): {"podcastId": "terrified", "guid": "guid-4", "kind": "story", "traits": []},
        }
        page = review_page(pending, tagged)
        self.assertIn("Rain On A Tent\n    sound: rain, wind    [sleepy-noises / guid-1]\n", page)
        self.assertIn("The House\n    horror    [terrified / guid-3]\n", page)
        self.assertIn("Nothing Fits\n    story: no traits    [terrified / guid-4]\n", page)
        self.assertNotIn("Never Answered", page)

    def test_a_title_cannot_leave_the_code_block(self):
        pending = [("sleepy-noises", "guid`1", "Rain ```\n@everyone [x](https://evil.example)", "")]
        tagged = {("sleepy-noises", "guid`1"): {"podcastId": "sleepy-noises", "guid": "guid`1", "traits": ["rain"]}}
        page = review_page(pending, tagged)
        self.assertEqual(page.count("```"), 2)  # the block's own two fences
        inside = page.split("```")[1]
        self.assertIn("@everyone", inside)
        self.assertIn("guid'1", inside)

    def test_a_long_list_stops_where_a_pull_request_does(self):
        pending = [("sleepy-noises", f"guid-{i}", "T" * 300, "") for i in range(400)]
        tagged = {(p, g): {"podcastId": p, "guid": g, "kind": "sound", "traits": ["rain"]} for p, g, _, _ in pending}
        page = review_page(pending, tagged)
        self.assertLess(len(page), 65536)
        self.assertLessEqual(len(page), MAX_REVIEW_CHARS + 100)
        shown = page.count("sound: rain")
        self.assertGreater(shown, 100)
        self.assertIn(f"And {400 - shown} more, in the changes to `tags.json` only.", page)
        self.assertEqual(page.count("```"), 2)


class Main(unittest.TestCase):
    """The whole script in an empty folder, the feed and Claude stood in for."""

    def run_main(self, *arguments, tags=None, tag=None):
        def fake_tagging(pending):
            return {
                (podcast_id, guid): {"podcastId": podcast_id, "guid": guid, "kind": "sound", "traits": ["rain"]}
                for podcast_id, guid, _, _ in pending
            }

        items = [item(title=f"Rain {i}", guid=f"guid-{i}") for i in (3, 1, 2)]
        tagging = mock.Mock(side_effect=tag or fake_tagging)
        out = io.StringIO()
        before = os.getcwd()
        with tempfile.TemporaryDirectory() as folder:
            os.chdir(folder)
            try:
                with open("catalog.json", "w", encoding="utf-8") as file:
                    json.dump({"podcasts": [SHOW]}, file)
                if tags is not None:
                    with open("tags.json", "w", encoding="utf-8") as file:
                        json.dump(tags, file)
                with (
                    mock.patch.object(tag_episodes, "fetch_feed", return_value=feed(*items)),
                    mock.patch.object(tag_episodes, "tag_with_claude", tagging),
                    mock.patch.object(tag_episodes.sys, "argv", ["tag_episodes.py", *arguments]),
                    mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "not-a-key"}),
                    redirect_stdout(out),
                ):
                    tag_episodes.main()
                written = None
                if os.path.exists("tags.json"):
                    with open("tags.json", encoding="utf-8") as file:
                        written = json.load(file)
                review = None
                if os.path.exists("review.md"):
                    with open("review.md", encoding="utf-8") as file:
                        review = file.read()
            finally:
                os.chdir(before)
        return written, review, tagging, out.getvalue()

    def test_new_entries_join_the_old_ones_sorted(self):
        old = {"podcastId": "sleepy-noises", "guid": "guid-2", "kind": "story", "traits": ["calm"]}
        written, review, tagging, said = self.run_main(
            "--review", "review.md", tags={"version": 1, "updatedAt": None, "episodes": [old]}
        )
        self.assertEqual([e["guid"] for e in written["episodes"]], ["guid-1", "guid-2", "guid-3"])
        self.assertEqual(written["episodes"][1], old)  # an entry that was there is never changed
        self.assertEqual([guid for _, guid, _, _ in tagging.call_args.args[0]], ["guid-3", "guid-1"])
        jsonschema.validate(written, schema(), format_checker=jsonschema.FormatChecker())
        self.assertIn("Rain 3\n    sound: rain    [sleepy-noises / guid-3]", review)
        self.assertIn("Added 2 entries to tags.json", said)

    def test_a_dry_run_lists_and_sends_nothing(self):
        written, _, tagging, said = self.run_main("--dry-run")
        self.assertIsNone(written)
        tagging.assert_not_called()
        self.assertEqual(
            said.splitlines(),
            ["3 episodes without tags", "  sleepy-noises: Rain 3", "  sleepy-noises: Rain 1", "  sleepy-noises: Rain 2"],
        )

    def test_a_full_file_takes_no_more(self):
        full = [{"podcastId": "another-show", "guid": f"g{i}", "traits": []} for i in range(MAX_TAGGED_EPISODES - 1)]
        written, _, tagging, said = self.run_main(tags={"version": 1, "updatedAt": None, "episodes": full})
        self.assertEqual(len(written["episodes"]), MAX_TAGGED_EPISODES)
        self.assertEqual(len(tagging.call_args.args[0]), 1)
        self.assertIn("::warning::tags.json has room for 1 more episodes (it holds 20000 at most)", said)


if __name__ == "__main__":
    unittest.main()
