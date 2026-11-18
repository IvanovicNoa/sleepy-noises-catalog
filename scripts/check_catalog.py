"""Checks catalog.json and tags.json before they are published.

    python scripts/check_catalog.py [--previous OLD_CATALOG.json]

The Publish workflow runs it on every change; nothing is published unless
it passes. Everything wrong is said as a sentence, on the pull request where
GitHub can show it. It can be run on a computer too
(pip install -r scripts/requirements-check.txt).

Beyond the schemas, it checks what a schema cannot say:

- no id is used twice, and every show's category exists;
- no show uses an id the app keeps for itself ("premium", "premium-…");
- tags.json names no episode twice and no show the catalog never had
  (the tags of a retired show may stay: nothing reads them);
- with --previous (the catalog as it was before this change): no show
  disappears by accident. The app keeps a listener's likes, progress and
  downloads under the show's id, so an id may only leave "podcasts" by
  moving to "retired", and an id in "retired" stays there for good.
  A typo in an id, or a renamed id, is caught here instead of on phones.
"""

import argparse
import json
import sys

import jsonschema

from workflow_log import error, one_line, warn

MOST_PROBLEMS_SAID = 20


def load(path):
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def schema_problems_in(document, schema, name):
    """What the schema refuses in `document`, a sentence each, with where."""
    validator_for = jsonschema.validators.validator_for(schema)
    validator_for.check_schema(schema)
    validator = validator_for(schema, format_checker=jsonschema.FormatChecker())
    found = []
    for fault in sorted(validator.iter_errors(document), key=lambda e: [str(step) for step in e.absolute_path]):
        where = " → ".join(str(step) for step in fault.absolute_path) or "the top"
        # A message quotes the value it refuses, however long that is.
        said = one_line(fault.message)
        found.append(f"{name}, at {where}: {said if len(said) <= 300 else said[:300] + '…'}")
    return found


def problems_in(catalog, previous=None):
    """Everything wrong with the catalog, as sentences. Empty: all good."""
    found = []
    shows = [p["id"] for p in catalog["podcasts"]]
    rows = [c["id"] for c in catalog["categories"]]
    retired = catalog.get("retired", [])

    for what, ids in (("show", shows), ("category", rows)):
        twice = sorted({i for i in ids if ids.count(i) > 1})
        if twice:
            found.append(f"{what} id used twice: {', '.join(twice)}")
    for show in catalog["podcasts"]:
        if show["categoryId"] not in rows:
            found.append(f"show \"{show['id']}\" is in category \"{show['categoryId']}\", which does not exist")

    # The app's own ids: a member's premium shows and the row they sit in.
    for show_id in shows + retired:
        if show_id.startswith("premium-"):
            found.append(f"\"{show_id}\": ids starting with \"premium-\" belong to the app")
    if "premium" in rows:
        found.append("the category id \"premium\" belongs to the app")

    both = sorted(set(shows) & set(retired))
    if both:
        found.append(f"listed as a show and as retired: {', '.join(both)}")

    if previous is not None:
        before = {p["id"] for p in previous.get("podcasts", [])}
        gone = sorted(before - set(shows) - set(retired))
        for show_id in gone:
            found.append(
                f"the show \"{show_id}\" was published and is now missing. If its id was "
                "mistyped or changed, put the old id back (an id never changes). If the show "
                f"is really gone for good, add \"{show_id}\" to \"retired\": phones then delete "
                "it, with what listeners saved for it."
            )
        back = sorted(set(previous.get("retired", [])) - set(retired))
        for show_id in back:
            found.append(f"\"{show_id}\" was retired and must stay in \"retired\" (ids are never used again)")
    return found


def tag_problems_in(tags, catalog):
    """Everything wrong with tags.json that its schema cannot say."""
    found = []
    keys = [(e["podcastId"], e["guid"]) for e in tags["episodes"]]
    if len(keys) != len(set(keys)):
        found.append("tags.json names an episode twice (same podcastId and guid)")
    # A retired show's tags are harmless leftovers: taking a show out must
    # not need a second change to another file.
    known = {p["id"] for p in catalog["podcasts"]} | set(catalog.get("retired", []))
    unknown = sorted({podcast for podcast, _ in keys} - known)
    if unknown:
        found.append(f"tags.json names shows that are not in the catalog: {', '.join(unknown)}")
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--previous", help="the catalog as it was before this change")
    args = parser.parse_args()

    documents = {}
    for name in ("catalog.json", "tags.json"):
        try:
            documents[name] = load(name)
        except json.JSONDecodeError as fault:
            # The likeliest slip in a file edited by hand: a comma, a quote.
            error(f"{name} is not valid JSON: {fault.msg} (line {fault.lineno}, column {fault.colno})", name, fault.lineno)
            return 1
    catalog, tags = documents["catalog.json"], documents["tags.json"]

    catalog_faults = schema_problems_in(catalog, load("catalog.schema.json"), "catalog.json")
    tag_faults = schema_problems_in(tags, load("tags.schema.json"), "tags.json")
    found = catalog_faults + tag_faults
    previous = load(args.previous) if args.previous else None
    # What follows reads ids out of the files: only once their shape is right.
    if not catalog_faults:
        found += problems_in(catalog, previous)
        if not tag_faults:
            found += tag_problems_in(tags, catalog)

    for problem in found[:MOST_PROBLEMS_SAID]:
        error(problem)
    if len(found) > MOST_PROBLEMS_SAID:
        error(f"and {len(found) - MOST_PROBLEMS_SAID} more problems")
    if found:
        return 1

    placeholders = [p["id"] for p in catalog["podcasts"] if "REPLACE-WITH" in p["feedUrl"]]
    if placeholders:
        warn(f"placeholder feed URLs still in use: {placeholders}")
    compared = "compared with the published one" if previous is not None else "nothing to compare with"
    print(
        f"catalog OK: {len(catalog['podcasts'])} shows, "
        f"{len(catalog.get('retired', []))} retired ({compared})"
    )
    print(f"tags OK: {len(tags['episodes'])} episodes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
