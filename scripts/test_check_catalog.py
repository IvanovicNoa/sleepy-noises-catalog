"""Tests for check_catalog.py: python -m unittest discover -s scripts"""

import copy
import unittest

from check_catalog import problems_in

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


if __name__ == "__main__":
    unittest.main()
