"""tag_episodes.py with the real Anthropic SDK, against a stand-in for the API.

    python -m unittest discover -s scripts   (after
    pip install --require-hashes --only-binary :all: -r scripts/requirements-tagging.txt)

Skipped where the SDK is not installed. No key, no network, no cost: a
small server on this computer answers the four calls the script makes
(create, retrieve, cancel, results) in the shapes the API uses. It is here
for the day the SDK is updated: if the script and the SDK no longer fit,
this fails on the pull request and not on a Monday morning.
"""

import importlib.util
import io
import json
import os
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import tag_episodes

NOW = "2026-11-11T12:00:00Z"
RAIN = '{"kind": "sound", "traits": ["rain"]}'


def message(text, stop_reason="end_turn"):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": tag_episodes.MODEL,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def succeeded(text=RAIN, stop_reason="end_turn"):
    return {"type": "succeeded", "message": message(text, stop_reason)}


class StandIn:
    """The Message Batches API, as much of it as the script uses."""

    def __init__(self, answers=None, ends_after=2, never_ends=False, failing_polls=0, results_break_after=None):
        self.answers = answers or {}  # custom_id -> result; the others succeed
        self.ends_after = ends_after  # polls until the batch has ended
        self.never_ends = never_ends  # ... unless it is cancelled
        self.failing_polls = failing_polls  # this many polls in a row answer 500, once
        self.results_break_after = results_break_after  # lines sent before the connection drops
        self.requests = []
        self.headers = {}
        self.creates = self.polls = self.cancels = self.failed = 0
        self.cancelled_at = None

    def batch(self, port):
        if self.cancelled_at is not None:
            ended = self.polls >= self.cancelled_at + 2
        else:
            ended = not self.never_ends and self.polls >= self.ends_after
        status = "ended" if ended else ("canceling" if self.cancelled_at is not None else "in_progress")
        count = len(self.requests)
        return {
            "id": "msgbatch_1",
            "type": "message_batch",
            "processing_status": status,
            "request_counts": {
                "processing": 0 if ended else count,
                "succeeded": count if ended else 0,
                "errored": 0,
                "canceled": 0,
                "expired": 0,
            },
            "created_at": NOW,
            "expires_at": NOW,
            "ended_at": NOW if ended else None,
            "archived_at": None,
            "cancel_initiated_at": None if self.cancelled_at is None else NOW,
            "results_url": f"http://127.0.0.1:{port}/v1/messages/batches/msgbatch_1/results" if ended else None,
        }

    def results(self):
        lines = []
        for index, request in enumerate(self.requests):
            if self.cancelled_at is not None and index >= len(self.requests) // 2:
                result = {"type": "canceled"}  # the half it had not come to
            else:
                result = self.answers.get(request["custom_id"], succeeded())
            lines.append(json.dumps({"custom_id": request["custom_id"], "result": result}))
        return lines

    def __enter__(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *arguments):
                pass

            def answer(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                port = self.server.server_address[1]
                if self.path == "/v1/messages/batches":
                    api.creates += 1
                    api.headers = {name.lower(): value for name, value in self.headers.items()}
                    api.requests = json.loads(body)["requests"]
                    self.answer(200, api.batch(port))
                elif self.path == "/v1/messages/batches/msgbatch_1/cancel":
                    api.cancels += 1
                    api.cancelled_at = api.polls
                    self.answer(200, api.batch(port))
                else:
                    self.answer(404, {"type": "error", "error": {"type": "not_found_error", "message": self.path}})

            def do_GET(self):
                port = self.server.server_address[1]
                if self.path == "/v1/messages/batches/msgbatch_1/results":
                    lines = api.results()
                    if api.results_break_after is None:
                        data = ("\n".join(lines) + "\n").encode()
                        promised = len(data)
                    else:  # promise more than is sent, then hang up
                        data = ("\n".join(lines[: api.results_break_after]) + "\n").encode()
                        promised = len(data) + 5000
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-jsonl")
                    self.send_header("Content-Length", str(promised))
                    self.end_headers()
                    self.wfile.write(data)
                    if promised != len(data):
                        self.wfile.flush()
                        self.connection.close()
                elif self.path == "/v1/messages/batches/msgbatch_1":
                    if api.polls == 1 and api.failed < api.failing_polls:
                        api.failed += 1
                        self.answer(500, {"type": "error", "error": {"type": "api_error", "message": "later"}})
                    else:
                        api.polls += 1
                        self.answer(200, api.batch(port))
                else:
                    self.answer(404, {"type": "error", "error": {"type": "not_found_error", "message": self.path}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *details):
        self.server.shutdown()
        self.server.server_close()
        return False


PENDING = [("sleepy-noises", f"guid-{i}", f"Rain {i}", "Rain on a tent, all night.") for i in range(6)]


@unittest.skipUnless(importlib.util.find_spec("anthropic"), "the Anthropic SDK is not installed here")
class WithTheSdk(unittest.TestCase):
    def tag(self, api):
        """(what tag_with_claude returned, what it printed)."""
        out = io.StringIO()
        address = f"http://127.0.0.1:{api.server.server_address[1]}"
        environment = {
            "ANTHROPIC_API_KEY": "not-a-key",
            "ANTHROPIC_BASE_URL": address,
            "NO_PROXY": "127.0.0.1",
            "no_proxy": "127.0.0.1",
        }
        with (
            mock.patch.dict(os.environ, environment),
            mock.patch.object(tag_episodes, "POLL_SECONDS", 1),
            mock.patch.object(tag_episodes, "POLL_LIMIT_SECONDS", 3),
            mock.patch.object(tag_episodes, "CANCEL_LIMIT_SECONDS", 5),
            # The script's waits, and the SDK's between its tries.
            mock.patch.object(tag_episodes.time, "sleep"),
            redirect_stdout(out),
        ):
            tagged = tag_episodes.tag_with_claude(PENDING)
        return tagged, out.getvalue().splitlines()

    def test_one_batch_asked_about_until_it_ends_then_read(self):
        with StandIn(ends_after=2) as api:
            tagged, said = self.tag(api)
        self.assertEqual((api.creates, api.polls, api.cancels), (1, 3, 0))  # the third poll is the results' own
        self.assertEqual(len(tagged), 6)
        self.assertEqual(
            tagged[("sleepy-noises", "guid-0")],
            {"podcastId": "sleepy-noises", "guid": "guid-0", "kind": "sound", "traits": ["rain"]},
        )
        self.assertEqual(
            said,
            ["Batch msgbatch_1: 6 episodes", "Done: 6 tagged, 0 errored, 0 canceled, 0 expired"],
        )

    def test_what_is_sent(self):
        with StandIn() as api:
            self.tag(api)
        # The key from the environment, in the header SDK 1.x sends it in. A
        # later major version sends "Authorization: Bearer" and speaks a newer
        # version of the API: moving to one is a change to make on purpose
        # (.github/dependabot.yml holds it back), and this line moves with it.
        self.assertEqual(api.headers.get("x-api-key"), "not-a-key")
        self.assertEqual([r["custom_id"] for r in api.requests], [f"ep-{i}" for i in range(6)])
        sent = api.requests[0]["params"]
        self.assertEqual(sent["model"], "claude-opus-5-5")
        self.assertEqual(sent["max_tokens"], 4000)
        self.assertEqual(
            sent["system"],
            [{"type": "text", "text": tag_episodes.SYSTEM, "cache_control": {"type": "ephemeral"}}],
        )
        self.assertEqual(
            sent["output_config"],
            {"effort": "low", "format": {"type": "json_schema", "schema": tag_episodes.OUTPUT_SCHEMA}},
        )
        # Title and description only, as data: nothing of the feed is an instruction.
        self.assertEqual(
            sent["messages"],
            [{"role": "user", "content": '{"title": "Rain 0", "description": "Rain on a tent, all night."}'}],
        )
        self.assertEqual(set(sent), {"model", "max_tokens", "system", "output_config", "messages"})

    def test_answers_that_cannot_be_used_cost_only_themselves(self):
        answers = {
            "ep-0": succeeded("null"),
            "ep-1": succeeded("", "refusal"),
            "ep-2": {"type": "errored", "error": {"type": "error", "error": {"type": "api_error", "message": "x"}}},
            "ep-3": succeeded('{"kind": "story", "traits": [{"a": 1}, "horror", "horror", "nope"]}'),
            "ep-4": {"type": "expired"},
        }
        with StandIn(answers) as api:
            tagged, said = self.tag(api)
        self.assertEqual(
            list(tagged.values()),
            [
                {"podcastId": "sleepy-noises", "guid": "guid-3", "kind": "story", "traits": ["horror"]},
                {"podcastId": "sleepy-noises", "guid": "guid-5", "kind": "sound", "traits": ["rain"]},
            ],
        )
        self.assertEqual(
            said[2:],
            [
                "::warning::sleepy-noises / Rain 0: an answer of another shape",
                "::warning::sleepy-noises / Rain 1: stopped (refusal)",
                "::warning::sleepy-noises / Rain 2: errored",
                "::warning::sleepy-noises / Rain 4: expired",
            ],
        )

    def test_a_batch_that_runs_too_long_is_cancelled_and_what_it_finished_is_kept(self):
        with StandIn(never_ends=True) as api:
            tagged, said = self.tag(api)
        self.assertEqual(api.cancels, 1)
        self.assertEqual(sorted(guid for _, guid in tagged), ["guid-0", "guid-1", "guid-2"])
        self.assertIn("::warning::Batch msgbatch_1 still running after 3s: cancelling what is left of it", said)
        self.assertEqual(sum("canceled" in line for line in said if line.startswith("::warning::sleepy")), 3)

    def test_a_server_that_fails_for_a_while_does_not_end_the_wait(self):
        # Four answers of 500 in a row: more than the SDK's usual two tries.
        with StandIn(failing_polls=4) as api:
            tagged, _ = self.tag(api)
        self.assertEqual(api.failed, 4)
        self.assertEqual(len(tagged), 6)

    def test_results_that_break_off_keep_what_was_read(self):
        with StandIn(results_break_after=3) as api:
            tagged, said = self.tag(api)
        self.assertGreaterEqual(len(tagged), 2)
        self.assertLess(len(tagged), 6)
        self.assertRegex(said[-1], r"^::warning::the results broke off \(\w+\): \d of 6 episodes read")


if __name__ == "__main__":
    unittest.main()
