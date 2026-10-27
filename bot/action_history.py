"""按动作和物理目标记忆结果，不把短命 ref、走动、体力消耗当作新任务。"""
from collections import deque
import hashlib
import json
import os
import time


FIELDS = ("kind", "name", "crop", "watered", "tilled", "diggable", "harvestable",
          "ready", "cropPhase", "cropDead", "health", "stump", "growthStage")


def at_tile(snap, entity):
    return [e for e in snap.get("entities") or []
            if e.get("tile") == entity.get("tile") and e.get("kind") != "npc"]


def target_state(snap, entity):
    rows = [json.dumps({k: e.get(k) for k in FIELDS}, sort_keys=True, ensure_ascii=False)
            for e in at_tile(snap, entity)]
    return sorted(rows)


def target_progress(action, before, after, entity, verified=False):
    if before.get("location") != after.get("location"):
        return False
    old, new = at_tile(before, entity), at_tile(after, entity)
    if action == "till":
        return not any(e.get("tilled") for e in old) and any(e.get("tilled") for e in new)
    if action == "plant":
        return not any(e.get("crop") for e in old) and any(e.get("crop") for e in new)
    if action == "water":
        return not any(e.get("watered") for e in old) and any(e.get("watered") for e in new)
    if action == "harvest":
        return (any(e.get("crop") and (e.get("harvestable") or e.get("ready")) for e in old)
                and bool(new) and not any(e.get("crop") and (e.get("harvestable") or e.get("ready")) for e in new))
    # Mod 的 use 已验证树的 health/stump/falling；快照不一定暴露树血量。
    if action == "chop":
        return verified
    if action == "clear":
        return verified and not any(e.get("kind") == entity.get("kind")
                                    and e.get("name") == entity.get("name") for e in new)
    return False


class ActionHistory:
    def __init__(self, path=None, log=lambda text: None, clock=time.time, memory=None):
        self.path, self.log, self.clock = path, log, clock
        self.memory = memory
        self._import_checked = False
        self.context = None
        self.records = {}
        self.recent = deque(maxlen=24)
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict) or not isinstance(data.get("records"), dict):
                    raise ValueError("invalid history")
                if any(not isinstance(r, dict) or "state" not in r or "status" not in r
                       for r in data["records"].values()):
                    raise ValueError("invalid record")
                self.context = data["context"]
                self.records = data["records"]
                self.recent.extend(data.get("recent", []))
            except (OSError, ValueError, KeyError, TypeError):
                self.records = {}

    def observe(self, snap):
        date = snap.get("date") or {}
        context = [snap.get("instanceId"), snap.get("saveGeneration"),
                   date.get("year"), date.get("season"), date.get("day")]
        if self.memory is not None and not self._import_checked:
            self._import_checked = True
            if self.records:
                imported_snap = snap
                if context != self.context:
                    tag = hashlib.sha256(json.dumps(self.context).encode()).hexdigest()[:16]
                    imported_snap = {"instanceId": "unassigned-legacy-" + tag, "saveGeneration": 0,
                                     "location": "unassigned", "date": {}}
                self.memory.import_legacy("action-history:" + json.dumps(self.context), list(self.records.values()), imported_snap)
        if context != self.context:
            self.context = context
            self.records.clear()
            self.recent.clear()

    def key(self, snap, action, entity):
        tile = entity.get("tile") or {}
        return "%s@%s:%s,%s" % (action, snap.get("location"), tile.get("x"), tile.get("y"))

    def allowed(self, snap, action, entity):
        self.observe(snap)
        row = self.records.get(self.key(snap, action, entity))
        if not row or row["state"] != target_state(snap, entity):
            return True
        if row["status"] not in ("failed", "no_progress"):
            return True
        retry_at = row.get("retry_at")
        return retry_at is not None and self.clock() >= retry_at

    def record(self, before, after, action, entity, progress, error=""):
        self.observe(before)
        key = self.key(before, action, entity)
        state = target_state(after, entity)
        previous = self.records.get(key) or {}
        failures = (previous.get("failures", 0) if previous.get("state") == state else 0)
        failures = 0 if progress else failures + 1
        transient = any(code in error for code in ("STALE_REF", "BUSY", "TRANSITION"))
        status = "progress" if progress and action == "chop" else "done" if progress else "failed" if error else "no_progress"
        row = {"label": key, "id": hashlib.sha256(json.dumps([self.context, key]).encode()).hexdigest()[:10],
               "status": status, "state": state, "failures": failures,
               "reason": error[:240] if error else "目标状态已改变" if progress else "目标没有可验证变化",
               "retry_at": self.clock() + 2 if transient and failures == 1 else None,
               "ts": self.clock()}
        self.records[key] = row
        self.recent.append({k: row[k] for k in ("label", "id", "status", "reason", "ts")})
        if self.memory is not None:
            self.memory.append("skill_target", after, source="executor", status=status,
                data={"skill": action, "target": {"tile": entity.get("tile"), "kind": entity.get("kind"), "name": entity.get("name")},
                      "label": key, "result": {"status": status, "progress": progress, "detail": row["reason"]},
                      "before_state": target_state(before, entity), "after_state": state})
        self.log("  [动作历史 %s #%s] %s：%s%s" % (
            key, row["id"], status, row["reason"],
            "；等目标状态变化再试，先换其他目标" if not progress and row["retry_at"] is None else ""))
        self.save()

    def summary(self):
        blocked = [r["label"] for r in self.records.values()
                   if r["status"] in ("failed", "no_progress") and r.get("retry_at") is None]
        return {"blocked_count": len(blocked), "blocked_targets": blocked[-12:],
                "recent": list(self.recent)[-12:]}

    def save(self):
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(self.path + ".tmp", "w", encoding="utf-8") as f:
                json.dump({"context": self.context, "records": self.records,
                           "recent": list(self.recent)}, f, ensure_ascii=False)
            os.replace(self.path + ".tmp", self.path)
        except OSError as exc:
            self.log("动作历史保存失败：%s" % type(exc).__name__)
