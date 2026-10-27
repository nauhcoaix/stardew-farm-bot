"""统一记忆：不可变证据事件 + 可重建的地图/连接索引，按真实存档隔离。"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

ENTITY_FIELDS = ("kind", "name", "tile", "passable", "diggable", "tilled", "watered", "crop",
                 "harvestable", "cropPhase", "cropDead", "refillable", "needsWater", "action",
                 "terrain", "objType", "objName", "health", "stump")


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def target_id(location, entity):
    identity = [location, entity.get("kind"), entity.get("name")]
    if entity.get("kind") != "npc":
        identity.append(entity.get("tile"))
    return "target:" + hashlib.sha256(dumps(identity).encode()).hexdigest()[:16]


def semantic(entity):
    return {k: entity[k] for k in ENTITY_FIELDS if k in entity}


def save_key(snap):
    if snap.get("saveId") is not None:
        return "save:%s/player:%s" % (snap["saveId"], snap.get("playerId", "local"))
    # 旧桥不能证明跨重启是同一存档，宁可隔离，不能把两个人的地图混在一起。
    return "session:%s:%s" % (snap.get("instanceId", "unknown"), snap.get("saveGeneration", 0))


def position(snap):
    p = (snap.get("player") or {}).get("position") or {}
    return {"x": p.get("x"), "y": p.get("y")}


def scene(snap):
    return {"save": save_key(snap), "instance": snap.get("instanceId"),
            "generation": snap.get("saveGeneration"), "location": snap.get("location")}


class MemoryStore:
    def __init__(self, path=":memory:", clock=time.time):
        self.path, self.clock = str(path), clock
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS events(
          id TEXT PRIMARY KEY, save_id TEXT NOT NULL, session_id TEXT, ts REAL,
          kind TEXT, status TEXT, location TEXT, action_key TEXT, fingerprint TEXT, body TEXT, search TEXT);
        CREATE INDEX IF NOT EXISTS event_recall ON events(save_id,kind,ts DESC);
        CREATE INDEX IF NOT EXISTS event_failures ON events(save_id,action_key,ts DESC);
        CREATE TABLE IF NOT EXISTS tiles(
          save_id TEXT, location TEXT, x INTEGER, y INTEGER, body TEXT, event_id TEXT,
          visited INTEGER DEFAULT 0, PRIMARY KEY(save_id,location,x,y));
        CREATE TABLE IF NOT EXISTS links(
          save_id TEXT, origin TEXT, destination TEXT, target TEXT, confirmed INTEGER,
          event_id TEXT, PRIMARY KEY(save_id,origin,destination,target));
        CREATE TABLE IF NOT EXISTS imports(save_id TEXT, name TEXT, PRIMARY KEY(save_id,name));
        CREATE TABLE IF NOT EXISTS cursors(save_id TEXT,session_id TEXT,seq INTEGER,last_point TEXT,
          PRIMARY KEY(save_id,session_id));
        -- 中文 bigram 倒排索引：LIKE '%词%' 只能整词命中，这里按 2-gram 建索引
        CREATE TABLE IF NOT EXISTS vectors(event_id TEXT PRIMARY KEY, save_id TEXT, dim INTEGER, blob BLOB);
        -- 中文 bigram 倒排索引：LIKE '%词%' 只能整词命中，这里按 2-gram 建索引
        CREATE TABLE IF NOT EXISTS grams(gram TEXT, event_id TEXT, save_id TEXT);
        CREATE INDEX IF NOT EXISTS gram_lookup ON grams(gram, save_id);
        CREATE INDEX IF NOT EXISTS gram_event ON grams(event_id);
        PRAGMA user_version=2;
        """)
        self.current = {}
        self.previous = None
        self._tiles = {}
        self._last_signature = None
        self.active_action = None
        self.evidence = []
        self.transition_target = None

    def append(self, kind, snap=None, *, status="observed", source="game", confidence="observed",
               data=None, evidence=None, action_key=None, fingerprint=None):
        snap = snap or self.current
        if source == "model":
            confidence = "hypothesis"
        elif source in ("player", "migration"):
            confidence = "reported"
        event = {"schema_version": 1, "id": uuid.uuid4().hex, "save_id": save_key(snap),
                 "session_id": "%s:%s" % (snap.get("instanceId"), snap.get("saveGeneration")),
                 "recorded_at": datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(),
                 "game_time": dict(snap.get("date") or {}, time=snap.get("time")),
                 "kind": kind, "source": source, "confidence": confidence, "status": status,
                 "location": snap.get("location"), "actor": position(snap),
                 "parent_id": self.active_action, "evidence": list(evidence or []), "data": data or {}}
        with self.db:
            self.db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                event["id"], event["save_id"], event["session_id"], self.clock(), kind, status,
                event["location"], action_key, fingerprint, dumps(event), dumps(data or {})[:16000]))
        if self.active_action:
            self.evidence.append(event["id"])
        self._index_grams(event["id"], event["save_id"], event)
        return event

    # ---------- L1/L2：bigram 索引 + TF-IDF 检索 ----------
    @staticmethod
    def _grams(text):
        """中文按 2-gram、英文数字按整词切；返回去重后的集合。"""
        import re
        t = str(text or "")
        out = set()
        for run in re.findall(r"[0-9A-Za-z_]+|[一-鿿]+", t):
            if run[0].isascii():
                if len(run) >= 2:
                    out.add(run.lower())
                continue
            if len(run) == 1:
                out.add(run)
            for i in range(len(run) - 1):
                out.add(run[i:i + 2])
        return out

    def _index_grams(self, event_id, scope, event):
        text = " ".join(str(event.get("data", {}).get(k) or "") for k in
                        ("text", "goal", "reply", "skill", "detail", "reason"))
        text += " " + str(event.get("location") or "")
        text += " " + str(event.get("kind") or "")
        grams = self._grams(text)
        if not grams:
            return
        with self.db:
            self.db.executemany("INSERT INTO grams VALUES(?,?,?)",
                                [(g, event_id, scope) for g in grams])

    # ---------- 向量检索（bge-small-zh ONNX，可缺省降级） ----------
    @staticmethod
    def _event_text_for_vec(event):
        data = event.get("data") or {}
        parts = [str(data.get(k) or "") for k in ("text", "goal", "reply", "skill", "detail", "reason")]
        return " ".join(p for p in parts if p).strip()[:600]

    def embed_events(self, ids=None, limit=400, batch=16):
        """给指定/缺失向量的事件算向量；模型不可用时返回 0。"""
        import embeddings
        if not embeddings.available():
            return 0
        if ids:
            marks = ",".join("?" for _ in ids)
            rows = self.db.execute(
                "SELECT id,save_id,body FROM events WHERE id IN (%s) "
                "AND id NOT IN (SELECT event_id FROM vectors)" % marks, list(ids)).fetchall()
        else:
            rows = self.db.execute(
                "SELECT id,save_id,body FROM events WHERE id NOT IN (SELECT event_id FROM vectors) "
                "ORDER BY ts DESC LIMIT ?", (int(limit),)).fetchall()
        if not rows:
            return 0
        texts, keep = [], []
        for r in rows:
            try:
                body = json.loads(r["body"])
            except Exception:
                continue
            t = self._event_text_for_vec(body)
            if not t:
                continue
            texts.append(t)
            keep.append((r["id"], r["save_id"]))
        if not texts:
            return 0
        vecs = embeddings.encode(texts, batch=batch)
        if vecs is None:
            return 0
        with self.db:
            self.db.executemany(
                "INSERT OR REPLACE INTO vectors(event_id,save_id,dim,blob) VALUES(?,?,?,?)",
                [(eid, sc, int(vecs.shape[1]), embeddings.to_blob(vecs[i])) for i, (eid, sc) in enumerate(keep)])
        return len(keep)

    def vector_scores(self, query, ids):
        """查询向量 vs 一批事件的余弦；模型不可用返回 None。"""
        import embeddings
        if not embeddings.available() or not ids:
            return None
        qv = embeddings.encode(query)
        if qv is None:
            return None
        marks = ",".join("?" for _ in ids)
        rows = self.db.execute("SELECT event_id,blob FROM vectors WHERE event_id IN (%s)" % marks, list(ids)).fetchall()
        if not rows:
            return None
        import numpy as np
        mat = np.vstack([embeddings.from_blob(r["blob"]) for r in rows])
        sims = embeddings.cosine(qv[0], mat)
        return {r["event_id"]: float(sims[i]) for i, r in enumerate(rows)}

    def rebuild_grams(self, snap=None):
        """重建 bigram 索引（剪枝/迁移后调用）。"""
        with self.db:
            self.db.execute("DELETE FROM grams")
        rows = self.db.execute("SELECT id,save_id,body,search,location,kind FROM events").fetchall()
        n = 0
        for r in rows:
            try:
                body = json.loads(r[2])
            except Exception:
                body = {}
            body.setdefault("data", {})
            body["search"] = r[3]
            body["location"] = r[4]
            body["kind"] = r[5]
            self._index_grams(r[0], r[1], body)
            n += 1
        return n

    def observe(self, snap):
        if not snap or snap.get("ok") is False:
            return None
        scope, location = save_key(snap), snap.get("location")
        native_positions = self._record_trajectory(snap)
        cache_key = (scope, location)
        if cache_key not in self._tiles:
            self._tiles[cache_key] = {(r["x"], r["y"]): json.loads(r["body"]) for r in self.db.execute(
                "SELECT x,y,body FROM tiles WHERE save_id=? AND location=?", cache_key)}
        cache = self._tiles[cache_key]
        changed = []
        for e in snap.get("entities") or []:
            t = e.get("tile") or {}
            if not isinstance(t.get("x"), int) or not isinstance(t.get("y"), int):
                continue
            # NPC/出口叠在地块上时保留地块通行信息，独立目标见当次观察。
            if e.get("kind") in ("npc", "exit", "door", "bed", "furniture"):
                continue
            row = semantic(e)
            key = (t["x"], t["y"])
            if cache.get(key) != row:
                cache[key] = row
                changed.append(row)
        menu = snap.get("menu") or {}
        summary = {"player": snap.get("player"), "inventory": snap.get("inventory") or [],
                   "menu": {"identity": menu.get("identity"), "type": menu.get("type"), "text": menu.get("text"),
                            "options": [o.get("label") for o in menu.get("options") or []]},
                   "targets": [semantic(e) for e in snap.get("entities") or [] if e.get("kind") in ("npc", "exit", "door", "bed", "furniture")]}
        signature = dumps([scope, snap.get("instanceId"), location, summary, changed])
        self.current = snap
        if signature == self._last_signature and not changed:
            return None
        self._last_signature = signature if not changed else None
        event = self.append("observation", snap, data=dict(summary, changed_tiles=changed,
                            observed_scope=snap.get("observation") or {}))
        with self.db:
            for row in changed:
                t = row["tile"]
                self.db.execute("INSERT INTO tiles VALUES(?,?,?,?,?,?,0) ON CONFLICT(save_id,location,x,y) "
                                "DO UPDATE SET body=excluded.body,event_id=excluded.event_id",
                                (scope, location, t["x"], t["y"], dumps(row), event["id"]))
            p = position(snap)
            if isinstance(p["x"], int) and isinstance(p["y"], int):
                self.db.execute("INSERT INTO tiles VALUES(?,?,?,?,?,?,1) ON CONFLICT(save_id,location,x,y) "
                                "DO UPDATE SET visited=1", (scope, location, p["x"], p["y"],
                                dumps(cache.get((p["x"], p["y"])) or {"tile": p, "passable": True}), event["id"]))
        for e in snap.get("entities") or []:
            if e.get("kind") == "exit" or (e.get("name") or "").startswith("出口"):
                destination = (e.get("name") or "").replace("→", "->").split("->")[-1].strip()
                if destination and destination != location:
                    with self.db:
                        self.db.execute("INSERT INTO links VALUES(?,?,?,?,0,?) ON CONFLICT DO UPDATE SET event_id=excluded.event_id",
                                        (scope, location, destination, dumps(semantic(e)), event["id"]))
        previous = self.previous
        if previous and save_key(previous) == scope and previous.get("instanceId") == snap.get("instanceId"):
            if previous.get("location") != location:
                transition = self.append("transition", snap, data={"from": previous.get("location"), "to": location,
                    "from_tile": position(previous), "to_tile": position(snap), "trigger": self.transition_target}, evidence=[event["id"]])
                with self.db:
                    self.db.execute("UPDATE links SET confirmed=1,event_id=? WHERE save_id=? AND origin=? AND destination=?",
                                    (transition["id"], scope, previous.get("location"), location))
                    if self.transition_target:
                        self.db.execute("INSERT INTO links VALUES(?,?,?,?,1,?) ON CONFLICT DO UPDATE SET confirmed=1,event_id=excluded.event_id",
                            (scope, previous.get("location"), location, dumps(self.transition_target), transition["id"]))
            elif not native_positions and position(previous) != position(snap):
                self.append("movement", snap, data={"from": position(previous), "to": position(snap),
                    "intermediate_tiles": [], "note": "只记录收到的现场坐标，不推测中间走过的格子"}, evidence=[event["id"]])
        self.previous = {k: snap.get(k) for k in ("saveId", "playerId", "instanceId", "saveGeneration", "location", "date", "time")}
        self.previous["player"] = {"position": dict(position(snap))}
        return event

    def _record_trajectory(self, snap):
        samples = snap.get("trajectory")
        if not isinstance(samples, list) or not samples:
            return False
        scope = save_key(snap)
        session = "%s:%s" % (snap.get("instanceId"), snap.get("saveGeneration"))
        cursor = self.db.execute("SELECT seq,last_point FROM cursors WHERE save_id=? AND session_id=?", (scope, session)).fetchone()
        last_seq, previous = (cursor[0], json.loads(cursor[1])) if cursor else (0, None)
        count = 0
        for sample in samples:
            seq, tile = sample.get("seq"), sample.get("tile") or {}
            if not isinstance(seq, int) or seq <= last_seq or not all(isinstance(tile.get(k), int) for k in ("x", "y")):
                continue
            point_snap = dict(snap, location=sample.get("location"), player={"position": tile},
                              date={k: sample.get(k) for k in ("year", "season", "day")}, time=sample.get("time"))
            gap = seq > last_seq + 1
            if gap:
                self.append("trace_gap", point_snap, status="unknown", data={"expected_sequence": last_seq + 1, "received_sequence": seq})
            kind = "movement" if previous and (previous.get("tile") != tile or previous.get("location") != sample.get("location")) else "position"
            event = self.append(kind, point_snap, data={"native_sequence": seq, "from": previous.get("tile") if previous else None,
                "from_location": previous.get("location") if previous else None, "to": tile,
                "intermediate_tiles": [], "sampling_gap": gap})
            with self.db:
                body = {"tile": tile, "passable": True, "observed_as_actor_position": True}
                self.db.execute("INSERT INTO tiles VALUES(?,?,?,?,?,?,1) ON CONFLICT(save_id,location,x,y) DO UPDATE SET visited=1",
                    (scope, sample.get("location"), tile["x"], tile["y"], dumps(body), event["id"]))
            last_seq, previous = seq, sample
            count += 1
        if count:
            with self.db:
                self.db.execute("INSERT INTO cursors VALUES(?,?,?,?) ON CONFLICT(save_id,session_id) "
                                "DO UPDATE SET seq=excluded.seq,last_point=excluded.last_point", (scope, session, last_seq, dumps(previous)))
        return count > 0

    def query(self, text="", kind=None, location=None, limit=12, snap=None):
        scope = save_key(snap or self.current)
        text = str(text or "")[:300]
        if text:
            scored = self._ranked(text, scope, kind=kind, location=location, limit=limit)
            if scored is not None:
                return scored
        clauses, values = ["save_id=?"], [scope]
        if text:
            clauses.append("search LIKE ? ESCAPE '\\'")
            text = str(text)[:300].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            values.append("%" + text + "%")
        if kind:
            clauses.append("kind=?")
            values.append(str(kind))
        if location:
            clauses.append("location=?")
            values.append(str(location))
        values.append(max(1, min(30, int(limit))))
        rows = self.db.execute("SELECT body FROM events WHERE " + " AND ".join(clauses) + " ORDER BY ts DESC,rowid DESC LIMIT ?", values)
        result = []
        for row in rows:
            event = json.loads(row[0])
            if len(dumps(event["data"])) > 8000:
                data = event["data"]
                event["data"] = {k: data[k] for k in ("skill", "args", "reason", "text", "from", "to", "from_tile", "to_tile") if k in data}
                if isinstance(data.get("result"), dict):
                    event["data"]["result"] = {k: data["result"].get(k) for k in ("status", "progress", "detail")}
                event["data"]["details_in_database"] = True
            result.append(event)
        return result

    KIND_WEIGHT = {"lesson": 3.0, "instruction": 2.2, "reflection": 1.8, "import": 2.0,
                   "plan": 0.7, "intent": 0.8, "observation": 0.9, "skill": 1.0,
                   "skill_call": 0.8, "skill_target": 0.6, "movement": 0.3, "transition": 1.5}

    def _ranked(self, text, scope, kind=None, location=None, limit=12):
        """bigram 候选 + TF-IDF 余弦 + 时间衰减 + 类型权重。返回 None 表示回退 LIKE。"""
        grams = self._grams(text)
        if not grams:
            return None
        marks = ",".join("?" for _ in grams)
        try:
            cand = self.db.execute(
                "SELECT event_id, COUNT(*) AS hit FROM grams WHERE save_id=? AND gram IN (%s) "
                "GROUP BY event_id ORDER BY hit DESC LIMIT 400" % marks,
                [scope] + list(grams)).fetchall()
        except Exception:
            return None
        if not cand:
            return []
        ids = [r[0] for r in cand]
        hits = {r[0]: r[1] for r in cand}
        try:
            self.embed_events(ids=ids[:200])          # 候选里没向量的先补上
        except Exception:
            pass
        try:
            vec_scores = self.vector_scores(text, ids[:400])
        except Exception:
            vec_scores = None
        marks = ",".join("?" for _ in ids)
        rows = self.db.execute(
            "SELECT id,kind,location,ts,body,search FROM events WHERE id IN (%s)" % marks, ids).fetchall()
        import math
        q = self._grams(text)
        now = self.clock()
        scored = []
        for r in rows:
            if kind and r["kind"] != kind:
                continue
            if location and r["location"] != location:
                continue
            doc = self._grams(" ".join([str(r["search"] or ""), self._event_text(json.loads(r["body"]))]))
            if not doc:
                continue
            inter = len(q & doc)
            cosine = inter / math.sqrt(max(1, len(q)) * max(1, len(doc)))
            age_h = max(0.0, (now - (r["ts"] or now)) / 3600.0)
            recency = 1.0 / (1.0 + age_h / 6.0)          # 6 小时半衰
            weight = self.KIND_WEIGHT.get(r["kind"], 1.0)
            vec = vec_scores.get(r["id"], 0.0) if vec_scores else 0.0
            if vec_scores:
                score = vec * 0.45 + cosine * 0.30 + recency * 0.15 + min(0.10, 0.01 * hits.get(r["id"], 0)) * weight
            else:
                score = cosine * 0.55 + recency * 0.25 + min(0.25, 0.02 * hits.get(r["id"], 0)) * weight
            scored.append((score, r))
        scored.sort(key=lambda x: -x[0])
        out = [self._shrink(json.loads(r["body"])) for _, r in scored[:max(1, min(30, int(limit)))]]
        return out

    @staticmethod
    def _event_text(body):
        data = body.get("data") or {}
        parts = [str(data.get(k) or "") for k in ("text", "goal", "reply", "skill", "detail", "reason")]
        parts.append(str(body.get("location") or ""))
        return " ".join(parts)

    @staticmethod
    def _shrink(event):
        if len(dumps(event.get("data"))) > 8000:
            data = event["data"]
            event["data"] = {k: data[k] for k in ("skill", "args", "reason", "text", "from", "to",
                                                  "from_tile", "to_tile", "goal") if k in data}
            if isinstance(data.get("result"), dict):
                event["data"]["result"] = {k: data["result"].get(k) for k in ("status", "progress", "detail")}
            event["data"]["details_in_database"] = True
        return event

    def lessons(self, limit=12, snap=None):
        """长期经验（L4 压缩出来的），永远先给模型看。

        教训是"游戏机制级"的知识，跨存档通用，所以不按 save_id 过滤。
        """
        rows = self.db.execute(
            "SELECT body FROM events WHERE kind='lesson' ORDER BY ts DESC LIMIT ?",
            (max(1, min(50, int(limit))),)).fetchall()
        out = []
        for (body,) in rows:
            try:
                out.append(json.loads(body)["data"].get("text") or "")
            except Exception:
                continue
        return [t for t in out if t]

    def add_lesson(self, text, source="consolidate", snap=None):
        return self.append("lesson", snap, source=source, confidence="observed",
                           data={"text": str(text)[:500]})

    def tiles(self, location=None, snap=None):
        snap = snap or self.current
        rows = self.db.execute("SELECT body,visited,event_id FROM tiles WHERE save_id=? AND location=?",
                               (save_key(snap), location or snap.get("location")))
        return [dict(json.loads(r[0]), visited=bool(r[1]), evidence_id=r[2]) for r in rows]

    def links(self, snap=None):
        rows = self.db.execute("SELECT origin,destination,target,confirmed,event_id FROM links WHERE save_id=?",
                               (save_key(snap or self.current),))
        return [{"from": r[0], "to": r[1], "target": json.loads(r[2]), "confirmed": bool(r[3]), "evidence_id": r[4]} for r in rows]

    def note(self, text, evidence=(), source="model", snap=None):
        snap = snap or self.current
        ids = list(evidence)[:12]
        for event_id in ids:
            if not self.db.execute("SELECT 1 FROM events WHERE id=? AND save_id=?", (str(event_id), save_key(snap))).fetchone():
                raise ValueError("笔记引用了不存在或其他存档的证据")
        return self.append("reflection" if source == "model" else "instruction", snap,
                           source=source, confidence="hypothesis" if source == "model" else "reported",
                           data={"text": str(text)[:2000]}, evidence=ids)

    def context(self, snap=None, query=""):
        snap = snap or self.current
        scope = save_key(snap)
        total, visited = self.db.execute("SELECT COUNT(*),COALESCE(SUM(visited),0) FROM tiles WHERE save_id=?", (scope,)).fetchone()
        recent = self.query(query, limit=8, snap=snap) if query else self.query(limit=8, snap=snap)
        # 给模型引用证据，不把大地图/完整历史塞入每个请求。
        compact = []
        for event in recent:
            row = {k: event[k] for k in ("id", "kind", "source", "confidence", "status", "location", "actor", "game_time")}
            data = event["data"]
            row["data"] = {k: data[k] for k in ("skill", "args", "text", "from", "to", "from_tile", "to_tile", "goal", "reply") if k in data}
            if isinstance(data.get("result"), dict):
                row["data"]["result"] = {k: data["result"].get(k) for k in ("status", "progress", "detail")}
            compact.append(row)
        return {"save_id": scope, "persistent_identity": snap.get("saveId") is not None,
                "event_types": ["observation", "position", "movement", "trace_gap", "transition", "intent", "plan", "skill_call", "skill_target", "skill", "reflection", "instruction"],
                "observed_tiles": total, "visited_tiles": visited, "recent": compact,
                "connections": self.links(snap)[:40]}

    def action_key(self, action):
        return dumps([action.get("action"), action.get("arg") or {}])

    def fingerprint(self, action, snap):
        arg = action.get("arg") or {}
        target = arg.get("target") or (arg.get("candidate") if str(arg.get("candidate", "")).startswith("target:") else None)
        facts = [semantic(e) for e in snap.get("entities") or [] if target_id(snap.get("location"), e) == target]
        if not target and "x" in arg:
            facts = [semantic(e) for e in snap.get("entities") or [] if e.get("tile") == {"x": arg["x"], "y": arg["y"]}]
        if not target and not facts:
            facts = [semantic(e) for e in snap.get("entities") or [] if e.get("kind") != "npc"]
        extra = {}
        if action.get("action") in ("move", "travel", "explore") and arg.get("mode", "path") == "path":
            extra = {"origin": position(snap), "known_tiles": len(self._tiles.get((save_key(snap), snap.get("location")), {})),
                     "connections": [(e["from"], e["to"], e["confirmed"]) for e in self.links(snap)]}
        if action.get("action") == "use" or (action.get("action") == "explore" and arg.get("skill") == "use"):
            slot = arg.get("slot", (snap.get("player") or {}).get("selectedSlot"))
            item = next((i for i in snap.get("inventory") or [] if i.get("slot") == slot), {})
            extra["selected_item"] = {k: item.get(k) for k in ("slot", "tool", "itemId", "name")}
            # 换工具可以重新试验；仅扣体力/减少水量不能解除同一失败的阻止。
        return dumps([snap.get("location"), facts, (snap.get("menu") or {}).get("text"), extra])

    def blocked(self, action, snap):
        if action.get("action") in ("memory", "observe", "wait"):
            return False
        rows = self.db.execute("SELECT status,fingerprint,ts FROM events WHERE save_id=? AND kind='skill' AND action_key=? "
                               "ORDER BY ts DESC,rowid DESC LIMIT 2", (save_key(snap), self.action_key(action))).fetchall()
        same = [r for r in rows if r[0] in ("failed", "no_progress", "blocked") and r[1] == self.fingerprint(action, snap)]
        return bool(same) and (len(same) >= 2 or self.clock() - same[0][2] < 30)

    def result(self, action, before, after, result):
        identity = (action.get("arg") or {}).get("target") or (action.get("arg") or {}).get("candidate")
        target = next((semantic(e) for e in before.get("entities") or [] if target_id(before.get("location"), e) == identity), None)
        event = self.append("skill", after, status=result.get("status", "unknown"), source="executor",
            data={"skill": action["action"], "args": action.get("arg") or {}, "reason": action.get("reason"),
                  "target": target,
                  "result": result, "before": {"scene": scene(before), "position": position(before)},
                  "after": {"scene": scene(after), "position": position(after)}},
            evidence=list(self.evidence[-24:]), action_key=self.action_key(action), fingerprint=self.fingerprint(action, after))
        return event

    def import_legacy(self, name, rows, snap):
        scope = save_key(snap)
        if self.db.execute("SELECT 1 FROM imports WHERE save_id=? AND name=?", (scope, name)).fetchone():
            return
        for row in rows:
            self.append("skill_target", snap, status=row.get("status", "unknown"), source="migration", confidence="reported",
                        data={"legacy_label": row.get("label"), "result": {"detail": row.get("reason")},
                              "legacy_state": row.get("state"), "note": "旧执行缓存迁移；无法确认归属的记录保存在独立命名空间"})
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO imports VALUES(?,?)", (scope, name))

    def close(self):
        self.db.close()
