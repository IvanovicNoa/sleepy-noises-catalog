"""Tests for workflow_log.py: python -m unittest discover -s scripts"""

import io
import unittest
from contextlib import redirect_stdout

from workflow_log import error, one_line, warn


def printed(function, *arguments):
    out = io.StringIO()
    with redirect_stdout(out):
        function(*arguments)
    return out.getvalue()


class OneLine(unittest.TestCase):
    def test_line_breaks_and_control_characters_become_one_space(self):
        self.assertEqual(one_line("Rain\n::error::On A\r\n\tTent"), "Rain ::error::On A Tent")
        self.assertEqual(one_line("a\x00b\x1bc\x7fd\x85e f g"), "a b c d e f g")
        self.assertEqual(one_line("  already   fine  "), "already fine")

    def test_takes_whatever_it_is_given(self):
        self.assertEqual(one_line(None), "None")
        self.assertEqual(one_line(12), "12")


class Commands(unittest.TestCase):
    def test_a_warning_is_one_command_whatever_it_holds(self):
        said = printed(warn, "show: feed not read (bad\n::error::line 100%0A::add-mask::x)")
        self.assertEqual(
            said,
            "::warning::show: feed not read (bad ::error::line 100%250A::add-mask::x)\n",
        )

    def test_an_error_can_point_at_a_line_of_a_file(self):
        self.assertEqual(printed(error, "two shows\nshare an id"), "::error::two shows share an id\n")
        self.assertEqual(
            printed(error, "not valid JSON", "catalog.json", 12),
            "::error file=catalog.json,line=12::not valid JSON\n",
        )
        self.assertEqual(printed(error, "wrong", "tags.json"), "::error file=tags.json::wrong\n")


if __name__ == "__main__":
    unittest.main()
