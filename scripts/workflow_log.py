"""What the scripts say to the workflow that runs them.

GitHub reads a run's output line by line, and a line that starts with "::"
is a command to it: an error or a warning to show on the run's page and on
the pull request, a value to hide. Much of what these scripts print comes
from outside (a feed's titles, a server's answers, a catalog somebody
proposes), so it never goes out as it came.
"""

import re


def one_line(text):
    """Any text as one line: every run of whitespace and control characters
    becomes one space. Printed after other words, it cannot start a line of
    its own, and so cannot be a command."""
    return re.sub(r"[\s\x00-\x1f\x7f-\x9f]+", " ", str(text)).strip()


def _command(name, message, where=""):
    # "%" starts an escape in a command's text ("%0A" is a line break).
    print(f"::{name}{where}::{one_line(message).replace('%', '%25')}")


def warn(message):
    """A warning on the run's page. Never fails the run."""
    _command("warning", message)


def error(message, file=None, line=None):
    """An error on the run's page; with `file` (and `line`), GitHub shows it
    on that line of the pull request. The caller fails the run."""
    where = ""
    if file is not None:
        where = f" file={file}" + (f",line={int(line)}" if line is not None else "")
    _command("error", message, where)
