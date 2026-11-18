import logging
import tempfile
import unittest
from pathlib import Path

import clan_monitor as cm


class DiffTests(unittest.TestCase):
    def test_first_run_has_no_changes(self):
        new = {"members": {"#A": {"name": "Ali", "th": 14}}}
        self.assertEqual(cm.diff_snapshots(None, new), [])

    def test_detects_join_leave_town_hall_hero_and_lab(self):
        old = {"members": {
            "#A": {"name": "Ali", "th": 14, "heroes": {"Barbarian King": 90}, "troops": {"Giant": 10}, "spells": {}},
            "#B": {"name": "Bob", "th": 12, "heroes": {}, "troops": {}, "spells": {}},
        }}
        new = {"members": {
            "#A": {"name": "Ali", "th": 15, "heroes": {"Barbarian King": 95}, "troops": {"Giant": 11}, "spells": {}},
            "#C": {"name": "Cat", "th": 13, "heroes": {}, "troops": {}, "spells": {}},
        }}
        text = "\n".join(cm.diff_snapshots(old, new))
        self.assertIn("Cat joined", text)
        self.assertIn("Bob (Town Hall 12) left the clan", text)
        self.assertIn("Ali upgraded Town Hall to Level 15", text)
        self.assertIn("Ali upgraded Barbarian King to Level 95", text)
        self.assertIn("Ali upgraded Giant to Level 11", text)

    def test_no_change_produces_no_lines(self):
        snap = {"members": {"#A": {"name": "Ali", "th": 14, "heroes": {"Barbarian King": 90}, "troops": {}, "spells": {}}}}
        self.assertEqual(cm.diff_snapshots(snap, snap), [])


class FormatTests(unittest.TestCase):
    def test_header_and_lines(self):
        text = cm.format_update(["Ali upgraded Giant to Level 11"])
        self.assertTrue(text.startswith("Clan Members Update ("))
        self.assertIn("MVT)\n\nAli upgraded Giant to Level 11", text)

    def test_split_message_keeps_lines_whole(self):
        lines = [f"Player {i} upgraded Giant to Level 11" for i in range(300)]
        chunks = cm.split_message("\n".join(lines), limit=500)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 500)
            for line in chunk.splitlines():
                self.assertTrue(line.startswith("Player "))


class RunCheckTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_file = cm.SNAPSHOT_FILE
        self._orig_delay = cm.PLAYER_DELAY_SECONDS
        cm.SNAPSHOT_FILE = Path(self._tmp.name) / "snap.json"
        cm.PLAYER_DELAY_SECONDS = 0
        self.logger = logging.getLogger("test")

    def tearDown(self):
        cm.SNAPSHOT_FILE = self._orig_file
        cm.PLAYER_DELAY_SECONDS = self._orig_delay
        self._tmp.cleanup()

    @staticmethod
    def make_fetch(members, players):
        async def fetch(endpoint):
            if endpoint.startswith("clans/"):
                return {"items": members}
            tag = endpoint.split("/")[-1].replace("%23", "#")
            if tag in players:
                return players[tag]
            return {"_api_error": {"reason": "requestException"}}
        return fetch

    async def test_first_run_silent_then_reports_changes(self):
        sent = []

        async def send(text):
            sent.append(text)

        members = [{"tag": "#A", "name": "Ali", "townHallLevel": 14}]
        players = {"#A": {"heroes": [{"name": "Barbarian King", "level": 90, "village": "home"}],
                          "troops": [], "spells": []}}
        fetch = self.make_fetch(members, players)

        await cm.run_check(fetch, send, self.logger, "%239G8RCVRV")
        self.assertEqual(sent, [])

        players["#A"]["heroes"][0]["level"] = 95
        members.append({"tag": "#B", "name": "Bob", "townHallLevel": 12})
        players["#B"] = {"heroes": [], "troops": [], "spells": []}

        await cm.run_check(fetch, send, self.logger, "%239G8RCVRV")
        self.assertEqual(len(sent), 1)
        self.assertIn("Clan Members Update", sent[0])
        self.assertIn("Bob joined", sent[0])
        self.assertIn("Ali upgraded Barbarian King to Level 95", sent[0])

    async def test_failed_player_fetch_causes_no_false_changes(self):
        sent = []

        async def send(text):
            sent.append(text)

        members = [{"tag": "#A", "name": "Ali", "townHallLevel": 14}]
        players = {"#A": {"heroes": [{"name": "Barbarian King", "level": 90, "village": "home"}],
                          "troops": [], "spells": []}}
        fetch = self.make_fetch(members, players)
        await cm.run_check(fetch, send, self.logger, "%239G8RCVRV")

        del players["#A"]  # player endpoint now fails
        await cm.run_check(fetch, send, self.logger, "%239G8RCVRV")
        self.assertEqual(sent, [])

    async def test_failed_send_does_not_save_snapshot(self):
        async def fetch_ok(endpoint):
            if endpoint.startswith("clans/"):
                return {"items": [{"tag": "#A", "name": "Ali", "townHallLevel": 14}]}
            return {"heroes": [], "troops": [], "spells": []}

        async def send_ok(text):
            pass

        async def send_fail(text):
            raise RuntimeError("telegram down")

        # Baseline with no changes
        await cm.run_check(fetch_ok, send_ok, self.logger, "%239G8RCVRV")
        baseline = cm.load_snapshot()

        # Change that fails to send: snapshot must stay as the baseline
        async def fetch_changed(endpoint):
            if endpoint.startswith("clans/"):
                return {"items": [{"tag": "#A", "name": "Ali", "townHallLevel": 15}]}
            return {"heroes": [], "troops": [], "spells": []}

        with self.assertRaises(RuntimeError):
            await cm.run_check(fetch_changed, send_fail, self.logger, "%239G8RCVRV")
        self.assertEqual(cm.load_snapshot(), baseline)


if __name__ == "__main__":
    unittest.main()