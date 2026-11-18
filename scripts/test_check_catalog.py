"""Tests for check_catalog.py: python -m unittest discover -s scripts"""

import copy
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import check_catalog
from check_catalog import problems_in, tag_problems_in

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CATALOG = {
    "version": 1,
    "categories": [{"id": "nature", "title": "Nature"}],
    "podcasts": [
        {"id": "sleepy-rain", "feedUrl": "https://feeds.megaphone.fm/A", "categoryId": "nature"},
        {"id": "sleepy-wind", "feedUrl": "https://feeds.megaphone.fm/B", "categoryId": "nature"},
    ],
    "retired": [],
}


def changed(**changes):
    catalog = copy.deepcopy(CATALOG)
    catalog.update(changes)
    return catalog


class CheckCatalog(unittest.TestCase):
    def test_a_good_catalog_has_no_problems(self):
        self.assertEqual(problems_in(CATALOG), [])
        self.assertEqual(problems_in(CATALOG, previous=CATALOG), [])

    def test_a_new_show_is_fine(self):
        shows = CATALOG["podcasts"] + [
            {"id": "sleepy-fire", "feedUrl": "https://feeds.megaphone.fm/C", "categoryId": "nature"}
        ]
        self.assertEqual(problems_in(changed(podcasts=shows), previous=CATALOG), [])

    def test_a_show_that_vanishes_is_caught(self):
        only_rain = changed(podcasts=CATALOG["podcasts"][:1])
        found = problems_in(only_rain, previous=CATALOG)
        self.assertEqual(len(found), 1)
        self.assertIn('"sleepy-wind" was published and is now missing', found[0])

    def test_a_mistyped_id_is_caught(self):
        typo = copy.deepcopy(CATALOG)
        typo["podcasts"][1]["id"] = "sleepy-wnid"
        self.assertTrue(problems_in(typo, previous=CATALOG))

    def test_retiring_a_show_is_the_way_out(self):
        retired = changed(podcasts=CATALOG["podcasts"][:1], retired=["sleepy-wind"])
        self.assertEqual(problems_in(retired, previous=CATALOG), [])

    def test_retired_is_for_good(self):
        before = changed(podcasts=CATALOG["podcasts"][:1], retired=["sleepy-wind"])
        forgotten = changed(podcasts=CATALOG["podcasts"][:1], retired=[])
        self.assertTrue(problems_in(forgotten, previous=before))
        both = changed(retired=["sleepy-wind"])
        self.assertTrue(problems_in(both))

    def test_ids_are_unique_and_categories_exist(self):
        twice = changed(podcasts=CATALOG["podcasts"] + CATALOG["podcasts"][:1])
        self.assertTrue(problems_in(twice))
        lost = copy.deepcopy(CATALOG)
        lost["podcasts"][0]["categoryId"] = "stories"
        self.assertTrue(problems_in(lost))

    def test_the_apps_own_ids_are_not_for_the_catalog(self):
        premium_show = copy.deepcopy(CATALOG)
        premium_show["podcasts"][0]["id"] = "premium-123"
        self.assertTrue(problems_in(premium_show))
        premium_row = changed(categories=[{"id": "premium", "title": "Premium"}])
        self.assertTrue(problems_in(premium_row))
        self.assertTrue(problems_in(changed(retired=["premium-9"])))


def tags(*episodes):
    return {"version": 1, "episodes": [{"podcastId": show, "guid": guid, "traits": []} for show, guid in episodes]}


class CheckTags(unittest.TestCase):
    def test_tags_of_the_catalogs_shows_are_fine(self):
        good = tags(("sleepy-rain", "a"), ("sleepy-rain", "b"), ("sleepy-wind", "a"))
        self.assertEqual(tag_problems_in(good, CATALOG), [])
        self.assertEqual(tag_problems_in(tags(), CATALOG), [])

    def test_an_episode_named_twice_is_caught(self):
        twice = tags(("sleepy-rain", "a"), ("sleepy-rain", "a"))
        self.assertEqual(len(tag_problems_in(twice, CATALOG)), 1)

    def test_a_show_the_catalog_never_had_is_caught(self):
        found = tag_problems_in(tags(("sleepy-rian", "a")), CATALOG)
        self.assertEqual(found, ["tags.json names shows that are not in the catalog: sleepy-rian"])

    def test_retiring_a_show_does_not_need_its_tags_removed(self):
        # Taking a show out is one change to one file (catalog.json); its
        # tags used to make that change fail.
        retired = changed(podcasts=CATALOG["podcasts"][:1], retired=["sleepy-wind"])
        left_over = tags(("sleepy-rain", "a"), ("sleepy-wind", "a"))
        self.assertEqual(tag_problems_in(left_over, retired), [])


class Main(unittest.TestCase):
    """The whole check, as the Publish workflow runs it, on files in an
    empty folder (with the real schemas)."""

    GOOD = dict(CATALOG, updatedAt="2026-11-11T12:00:00Z")

    def check(self, catalog, tags=None, previous=None):
        """(exit code, lines printed)."""
        before = os.getcwd()
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as folder:
            os.chdir(folder)
            try:
                for schema in ("catalog.schema.json", "tags.schema.json"):
                    shutil.copy(os.path.join(ROOT, schema), schema)
                files = {"catalog.json": catalog, "tags.json": tags or {"version": 1, "episodes": []}}
                arguments = ["check_catalog.py"]
                if previous is not None:
                    files["previous.json"] = previous
                    arguments += ["--previous", "previous.json"]
                for name, content in files.items():
                    with open(name, "w", encoding="utf-8") as file:
                        file.write(content if isinstance(content, str) else json.dumps(content))
                with mock.patch.object(check_catalog.sys, "argv", arguments), redirect_stdout(out):
                    code = check_catalog.main()
            finally:
                os.chdir(before)
        return code, out.getvalue().splitlines()

    def test_good_files_pass(self):
        code, said = self.check(self.GOOD, previous=self.GOOD)
        self.assertEqual(code, 0)
        self.assertEqual(
            said,
            ["catalog OK: 2 shows, 0 retired (compared with the published one)", "tags OK: 0 episodes"],
        )

    def test_the_catalog_in_this_repository_passes(self):
        with open(os.path.join(ROOT, "catalog.json"), encoding="utf-8") as file:
            catalog = file.read()
        with open(os.path.join(ROOT, "tags.json"), encoding="utf-8") as file:
            tags = file.read()
        code, said = self.check(catalog, tags)
        self.assertEqual((code, said[-1][:8]), (0, "tags OK:"))

    def test_a_slip_of_the_keyboard_is_shown_on_its_line(self):
        text = json.dumps(self.GOOD, indent=2)
        broken = text.replace('"categoryId": "nature"\n    },', '"categoryId": "nature"\n    }', 1)
        self.assertNotEqual(broken, text)
        code, said = self.check(broken)
        self.assertEqual(code, 1)
        self.assertEqual(len(said), 1)
        self.assertRegex(
            said[0],
            r"^::error file=catalog\.json,line=\d+::catalog\.json is not valid JSON: "
            r"Expecting ',' delimiter \(line \d+, column \d+\)$",
        )

    def test_what_the_schema_refuses_is_a_sentence_with_its_place(self):
        wrong = copy.deepcopy(self.GOOD)
        wrong["podcasts"][1]["feedUrl"] = "https://feeds.example.com/B"
        wrong["podcasts"][0]["premiumFeedUrl"] = "https://sleepynoises.supportingcast.fm/x"
        del wrong["updatedAt"]
        code, said = self.check(wrong)
        self.assertEqual(code, 1)
        self.assertEqual(len(said), 3)
        self.assertEqual(said[0], "::error::catalog.json, at the top: 'updatedAt' is a required property")
        self.assertIn("catalog.json, at podcasts → 0: Additional properties are not allowed", said[1])
        self.assertTrue(said[2].startswith("::error::catalog.json, at podcasts → 1 → feedUrl: "), said[2])

    def test_a_value_cannot_write_lines_of_its_own(self):
        wrong = copy.deepcopy(self.GOOD)
        wrong["podcasts"][0]["id"] = "rain\n::error file=x::made up\n::add-mask::y"
        wrong["podcasts"][1]["title"] = "x" * 5000
        code, said = self.check(wrong)
        self.assertEqual(code, 1)
        self.assertEqual(len(said), 2)
        for line in said:
            self.assertTrue(line.startswith("::error::catalog.json, at podcasts → "), line[:80])
            self.assertLess(len(line), 400)

    def test_many_problems_are_cut_short(self):
        wrong = copy.deepcopy(self.GOOD)
        wrong["podcasts"] = [dict(show, id=f"Show {i}") for i, show in enumerate(wrong["podcasts"] * 15)]
        code, said = self.check(wrong)
        self.assertEqual(code, 1)
        self.assertEqual(len(said), 21)
        self.assertEqual(said[-1], "::error::and 10 more problems")

    def test_what_a_schema_cannot_say_is_checked_once_the_shape_is_right(self):
        gone = dict(self.GOOD, podcasts=self.GOOD["podcasts"][:1])
        tags = {"version": 1, "episodes": [{"podcastId": "sleepy-wnid", "guid": "a", "traits": ["rain"]}]}
        code, said = self.check(gone, tags, previous=self.GOOD)
        self.assertEqual(code, 1)
        self.assertEqual(len(said), 2)
        self.assertIn('"sleepy-wind" was published and is now missing', said[0])
        self.assertEqual(said[1], "::error::tags.json names shows that are not in the catalog: sleepy-wnid")

    def test_tags_the_schema_refuses_are_said_too(self):
        tags = {"version": 1, "updatedAt": "yesterday", "episodes": [{"podcastId": "sleepy-rain", "guid": "a", "traits": ["snow"]}]}
        code, said = self.check(self.GOOD, tags)
        self.assertEqual(code, 1)
        self.assertEqual(len(said), 2)
        self.assertTrue(said[0].startswith("::error::tags.json, at episodes → 0 → traits → 0: 'snow' is not one of"), said[0])
        self.assertEqual(said[1], "::error::tags.json, at updatedAt: 'yesterday' is not a 'date-time'")


if __name__ == "__main__":
    unittest.main()
