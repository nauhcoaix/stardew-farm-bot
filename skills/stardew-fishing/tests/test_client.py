import copy
from datetime import datetime, timezone, timedelta
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

spec = importlib.util.spec_from_file_location("fish_skill", Path(__file__).parents[1] / "scripts/fish.py")
fish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fish)


def status(**kwargs):
    return dict(version=1, mode="game", observedUtc=datetime.now(timezone.utc).isoformat(),
                state="idle", active=False, runId="", **kwargs)


def publish(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)


class ClientTests(unittest.TestCase):
    def test_stale_state_is_not_a_live_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            row = status()
            row["observedUtc"] = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
            row["state"] = "completed"
            publish(runtime / "status.json", row)
            with self.assertRaisesRegex(ValueError, "过期"):
                fish.read_status(runtime)

    def test_start_waits_for_actual_matching_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            publish(runtime / "status.json", status())
            def game():
                deadline = time.monotonic() + 2
                while not (runtime / "command.json").exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                cmd = json.loads((runtime / "command.json").read_text())
                row = status(ack={"id": cmd["id"], "accepted": True})
                row.update(runId=cmd["id"], state="running", active=True)
                publish(runtime / "status.json", row)
            thread = threading.Thread(target=game)
            thread.start()
            result = fish.send(runtime, "start", {"maxCasts": 3}, ack_timeout=2)
            thread.join()
            self.assertTrue(result["active"])
            self.assertEqual(result["ack"]["id"], result["runId"])

    def test_pending_command_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            publish(runtime / "status.json", status())
            (runtime / "command.json").write_text('{"id":"original"}')
            with self.assertRaisesRegex(ValueError, "待处理"):
                fish.send(runtime, "start")
            self.assertEqual(json.loads((runtime / "command.json").read_text())["id"], "original")

    def test_replaced_run_is_not_reported_as_our_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            row = status()
            row.update(runId="different-run", state="completed")
            publish(runtime / "status.json", row)
            with self.assertRaisesRegex(ValueError, "替换"):
                fish.wait_run(runtime, "our-run", 1)

    def test_limits_reject_bad_times_and_unbounded_work(self):
        valid = dict(maxCasts=5, targetFish=2, maxSeconds=300, minEnergy=30, stopAt=2200)
        fish.validate_limits(valid)
        for key, bad in (("maxCasts", 0), ("maxSeconds", 99999), ("stopAt", 2260), ("targetFish", -1)):
            row = copy.copy(valid)
            row[key] = bad
            with self.assertRaises(ValueError):
                fish.validate_limits(row)


if __name__ == "__main__":
    unittest.main()
