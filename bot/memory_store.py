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
        -- 状态事实：同一 key 只保留最新值（和"故事"分开）
        CREATE TABLE IF NOT EXISTS facts(save_id TEXT, key TEXT, value TEXT, updated_ts REAL, source TEXT,
          PRIMARY KEY(save_id, key));
        -- 任务板：带前置条件，条件不满足就 blocked（而不是反复试）
        -- 地标：见过的障碍/水源/箱子/床/商店门（粗略全图）
        CREATE TABLE IF NOT EXISTS landmarks(save_id TEXT, location TEXT, kind TEXT, x INTEGER, y INTEGER,
          name TEXT, extra TEXT, seen_ts REAL, seen_day INTEGER, gone INTEGER DEFAULT 0,
          PRIMARY KEY(save_id, location, kind, x, y));
        -- 日程：每天 3~5 个阶段目标，而不是每 2.7 秒重选
        CREATE TABLE IF NOT EXISTS schedule(save_id TEXT, day INTEGER, slot INTEGER, slot_name TEXT,
          text TEXT, requires TEXT, status TEXT, updated_ts REAL,
          PRIMARY KEY(save_id, day, slot));
        -- 已种下的作物：离开视野也记得，用来浇水
        CREATE TABLE IF NOT EXISTS planted(save_id TEXT, location TEXT, x INTEGER, y INTEGER,
          crop TEXT, planted_ts REAL, watered_ts REAL, PRIMARY KEY(save_id, location, x, y));
        CREATE TABLE IF NOT EXISTS tasks(save_id TEXT, id TEXT, text TEXT, status TEXT,
          requires TEXT, blocked_reason TEXT, updated_ts REAL, PRIMARY KEY(save_id, id));
        -- 中文 bigram 倒排索引：LIKE '%词%' 只能整词命中，这里按 2-gram 建索引
        CREATE TABLE IF NOT EXISTS grams(gram TEXT, event_id TEXT, save_id TEXT);
        CREATE INDEX IF NOT EXISTS gram_lookup ON grams(gram, save_id);
        CREATE INDEX IF NOT EXISTS gram_event ON grams(event_id);
        PRAGMA user_version=2;
        """)
        for col, ddl in (("planted_day", "INTEGER"), ("watered_day", "INTEGER"),
                         ("phase_last_seen", "INTEGER"), ("phase_seen_day", "INTEGER"),
                         ("crop_name", "TEXT")):
            try:
                self.db.execute("ALTER TABLE planted ADD COLUMN %s %s" % (col, ddl))
            except sqlite3.OperationalError:
                pass          # 已经有了
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

    # ---------- 地标（粗略全图） ----------
    OBSTACLE_NAMES = ("杂草", "Grass", "草", "石头", "岩石", "嫩枝", "树枝", "木头", "树桩")

    @classmethod
    def _landmark_kind(cls, e):
        kind = str(e.get("kind") or "")
        name = str(e.get("name") or "")
        if e.get("refillable") or (kind in ("action", "object") and "水" in name):
            return "water"
        if "Chest" in str(e.get("objType")) or "箱" in name:
            return "chest"
        if kind == "bed":
            return "bed"
        if any(k in name for k in cls.OBSTACLE_NAMES):
            return "obstacle"
        if kind == "door" and ("店" in name or "Shop" in name):
            return "shop_door"
        return None

    def update_landmarks(self, snap):
        """视角内看到就记；在视野内确认没了才标 gone。返回 {kind: 变化数}。"""
        if not snap:
            return {}
        scope, loc = save_key(snap), snap.get("location")
        today = self.game_day(snap)
        pos = position(snap)
        seen, changes = set(), {}
        for e in snap.get("entities") or []:
            kind = self._landmark_kind(e)
            if not kind:
                continue
            t = e.get("tile") or {}
            x, y = t.get("x"), t.get("y")
            if x is None:
                continue
            key = (kind, x, y)
            seen.add(key)
            with self.db:
                self.db.execute(
                    "INSERT INTO landmarks(save_id,location,kind,x,y,name,extra,seen_ts,seen_day,gone) "
                    "VALUES(?,?,?,?,?,?,?,?,?,0) "
                    "ON CONFLICT(save_id,location,kind,x,y) DO UPDATE SET name=excluded.name, "
                    "seen_ts=excluded.seen_ts, seen_day=excluded.seen_day, gone=0",
                    (scope, loc, kind, x, y, str(e.get("name") or ""),
                     dumps(semantic(e))[:400], self.clock(), today))
                changes[kind] = changes.get(kind, 0) + 1
        # 视野半径内、之前记过、这次没出现的 -> gone
        rows = self.db.execute(
            "SELECT kind,x,y FROM landmarks WHERE save_id=? AND location=? AND gone=0", (scope, loc)).fetchall()
        with self.db:
            for r in rows:
                key = (r["kind"], r["x"], r["y"])
                if key in seen:
                    continue
                d = abs(r["x"] - pos["x"]) + abs(r["y"] - pos["y"])
                if d <= 14:            # 只敢在"确定在视野内"时判定消失
                    self.db.execute(
                        "UPDATE landmarks SET gone=1, seen_ts=? WHERE save_id=? AND location=? AND kind=? AND x=? AND y=?",
                        (self.clock(), scope, loc, r["kind"], r["x"], r["y"]))
                    changes[r["kind"] + "_gone"] = changes.get(r["kind"] + "_gone", 0) + 1
        return changes

    def landmarks(self, snap=None, kind=None, location=None, only_alive=True):
        snap = snap or self.current
        scope = save_key(snap)
        loc = location or snap.get("location")
        sql = "SELECT kind,x,y,name,seen_day FROM landmarks WHERE save_id=? AND location=?"
        args = [scope, loc]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if only_alive:
            sql += " AND gone=0"
        return [{"kind": r["kind"], "x": r["x"], "y": r["y"], "name": r["name"], "seen_day": r["seen_day"]}
                for r in self.db.execute(sql + " ORDER BY seen_ts DESC", args)]

    def landmark_digest(self, snap=None, near=None, limit=8):
        """给模型看的粗略全图摘要：每类几个 + 最近的几个坐标。"""
        snap = snap or self.current
        pos = position(snap)
        out = []
        for kind, label in (("obstacle", "障碍"), ("water", "水源"), ("chest", "箱子"),
                            ("bed", "床"), ("shop_door", "商店门")):
            items = self.landmarks(snap, kind=kind)
            if not items:
                continue
            items.sort(key=lambda i: abs(i["x"] - pos["x"]) + abs(i["y"] - pos["y"]))
            coord = "，".join("(%d,%d)" % (i["x"], i["y"]) for i in items[:limit])
            out.append("%s %d 个（最近的：%s）" % (label, len(items), coord))
        return out

    # ---------- 已种作物（持久化，供浇水用） ----------
    SEASON_INDEX = {"spring": 0, "summer": 1, "fall": 2, "winter": 3}

    @classmethod
    def game_day(cls, snap):
        """把 (year, season, day) 折算成绝对天数，方便做差。"""
        d = (snap or {}).get("date") or {}
        try:
            year = int(d.get("year") or 1)
            season = cls.SEASON_INDEX.get(str(d.get("season") or "").lower(), 0)
            day = int(d.get("day") or 1)
        except Exception:
            return None
        return (year - 1) * 4 * 28 + season * 28 + (day - 1)

    @classmethod
    def day_text(cls, day):
        if day is None:
            return "未知"
        year, rest = divmod(int(day), 4 * 28)
        season, dd = divmod(rest, 28)
        names = ["spring", "summer", "fall", "winter"]
        return "春%d" % (dd + 1) if season == 0 else "%s%d" % (names[season], dd + 1)

    def update_phases(self, snap):
        """视野内看到作物就更新阶段；顺便登记没记录过的作物。"""
        if not snap:
            return 0
        scope, loc = save_key(snap), snap.get("location")
        today = self.game_day(snap)
        n = 0
        with self.db:
            for e in snap.get("entities") or []:
                if not e.get("crop"):
                    continue
                t = e.get("tile") or {}
                x, y = t.get("x"), t.get("y")
                if x is None:
                    continue
                phase = int(e.get("cropPhase") or 0)
                name = str(e.get("name") or "")
                row = self.db.execute(
                    "SELECT planted_day FROM planted WHERE save_id=? AND location=? AND x=? AND y=?",
                    (scope, loc, x, y)).fetchone()
                if row is None:
                    # 视野里发现没登记过的作物（比如剧本里早种的），补登记
                    self.db.execute(
                        "INSERT INTO planted VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (scope, loc, x, y, str(e.get("crop") or ""), self.clock(), None,
                         today, None, phase, today, name))
                else:
                    self.db.execute(
                        "UPDATE planted SET phase_last_seen=?, phase_seen_day=?%s "
                        "WHERE save_id=? AND location=? AND x=? AND y=?"
                        % (", crop_name=?" if name else ""),
                        ([phase, today] + ([name] if name else []) + [scope, loc, x, y]))
                n += 1
        return n

    def crop_progress(self, snap=None):
        """算每块已种地的进度：种了几天、几天没浇、预计还差几天。"""
        snap = snap or self.current
        scope = save_key(snap)
        today = self.game_day(snap)
        rows = self.db.execute(
            "SELECT x,y,crop,crop_name,planted_day,watered_day,phase_last_seen FROM planted "
            "WHERE save_id=? AND phase_last_seen IS NOT NULL AND phase_last_seen < 4", (scope,)).fetchall()
        out = []
        for r in rows:
            planted_day = r["planted_day"] if r["planted_day"] is not None else today
            days_total = 4                      # 默认按防风草算，可被知识库覆盖
            name = r["crop_name"] or ""
            if "土豆" in name:
                days_total = 6
            elif "花椰菜" in name:
                days_total = 12
            grown = 0
            if today is not None and planted_day is not None:
                grown = max(0, today - planted_day)
            watered_today = (r["watered_day"] == today) if today is not None else False
            out.append({"x": r["x"], "y": r["y"], "crop": r["crop"], "name": name,
                        "planted_day": planted_day, "grown": grown, "days_total": days_total,
                        "left": max(0, days_total - grown), "phase": r["phase_last_seen"],
                        "watered_today": watered_today,
                        "overdue": grown >= days_total, "thirsty": not watered_today})
        return out

    def record_planted(self, snap, tile, crop=None):
        scope = save_key(snap)
        loc = snap.get("location")
        x, y = int(tile[0]), int(tile[1])
        today = self.game_day(snap)
        with self.db:
            self.db.execute("INSERT INTO planted(save_id,location,x,y,crop,planted_ts,watered_ts,"
                            "planted_day,watered_day,phase_last_seen,phase_seen_day) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                            "ON CONFLICT(save_id,location,x,y) DO UPDATE SET planted_ts=excluded.planted_ts, "
                            "planted_day=excluded.planted_day, crop=excluded.crop, "
                            "watered_ts=NULL, watered_day=NULL, phase_last_seen=0",
                            (scope, loc, x, y, str(crop or ""), self.clock(), None,
                             today, None, 0, today))
        return (loc, x, y)

    def mark_watered(self, snap, tiles):
        scope = save_key(snap)
        loc = snap.get("location")
        n = 0
        with self.db:
            today = self.game_day(snap)
            for t in tiles:
                cur = self.db.execute("UPDATE planted SET watered_ts=?, watered_day=? "
                                      "WHERE save_id=? AND location=? AND x=? AND y=?",
                                      (self.clock(), today, scope, loc, int(t[0]), int(t[1])))
                n += cur.rowcount
        return n

    def unwatered_planted(self, snap=None, location=None):
        """今天还没浇的已种作物（按游戏日判断；老数据没有日期时按"从未浇过"算）。"""
        snap = snap or self.current
        scope = save_key(snap)
        loc = location or snap.get("location")
        today = self.game_day(snap)
        rows = self.db.execute(
            "SELECT x,y,crop FROM planted WHERE save_id=? AND location=? "
            "AND (watered_day IS NULL OR ? IS NULL OR watered_day != ?)",
            (scope, loc, today, today)).fetchall()
        return [(r["x"], r["y"], r["crop"]) for r in rows]

    # ---------- 日程（每天 3~5 个阶段） ----------
    SLOT_NAMES = ["早上", "上午", "中午", "下午", "傍晚"]

    def build_schedule(self, snap):
        """按当前游戏日 + 事实生成当天阶段目标；已存在就不重排。"""
        if not snap:
            return []
        scope, today = save_key(snap), self.game_day(snap)
        if today is None:
            return []
        have = self.db.execute("SELECT COUNT(*) FROM schedule WHERE save_id=? AND day=?",
                               (scope, today)).fetchone()[0]
        facts = self.facts(snap)
        if have:
            return self.schedule(snap)
        stages = []
        if facts.get("crops_thirsty", 0) > 0:
            stages.append(("浇水", "把今天还没浇的作物都浇一遍", {"crops_thirsty": ">=1"}))
        if facts.get("planted_unwatered", 0) > 0:
            stages.append(("补浇", "按记忆坐标去浇已种未浇的地", {"planted_unwatered": ">=1"}))
        if facts.get("harvestable", 0) > 0:
            stages.append(("收成", "把成熟的作物收了", {"harvestable": ">=1"}))
        if facts.get("seeds", 0) > 0 and (facts.get("tillable", 0) > 0 or facts.get("tilled_empty", 0) > 0):
            stages.append(("种植", "把种子种到农舍附近的地里", {"seeds": ">=1"}))
        elif facts.get("seeds", 0) == 0:
            stages.append(("买种子", "去小镇皮埃尔种子店买防风草种子", None))
        if facts.get("tillable", 0) > 0:
            stages.append(("清障", "清农舍 12 格内的障碍，腾出可耕地", None))
        stages.append(("收工", "回农舍，时间不早于 21:00 就睡觉", None))
        stages = stages[:5]
        with self.db:
            for i, (name, text, req) in enumerate(stages):
                self.db.execute(
                    "INSERT OR IGNORE INTO schedule VALUES(?,?,?,?,?,?,?,?)",
                    (scope, today, i, self.SLOT_NAMES[i], text,
                     dumps(req or {}), "pending", self.clock()))
        return self.schedule(snap)

    def schedule(self, snap=None):
        snap = snap or self.current
        scope, today = save_key(snap), self.game_day(snap)
        if today is None:
            return []
        return [{"slot": r["slot"], "slot_name": r["slot_name"], "text": r["text"],
                 "status": r["status"],
                 "requires": json.loads(r["requires"] or "{}")}
                for r in self.db.execute(
                    "SELECT slot,slot_name,text,status,requires FROM schedule "
                    "WHERE save_id=? AND day=? ORDER BY slot", (scope, today))]

    def current_stage(self, snap=None):
        for st in self.schedule(snap):
            if st["status"] in ("pending", "blocked"):
                return st
        return None

    def advance_schedule(self, snap=None):
        """把当前阶段标完成；下一阶段自然接上。"""
        stages = self.schedule(snap)
        for st in stages:
            if st["status"] in ("pending", "blocked"):
                scope, today = save_key(snap or self.current), self.game_day(snap or self.current)
                with self.db:
                    self.db.execute("UPDATE schedule SET status='done', updated_ts=? "
                                    "WHERE save_id=? AND day=? AND slot=?",
                                    (self.clock(), scope, today, st["slot"]))
                return st
        return None

    def auto_advance_schedule(self, snap=None):
        """按事实自动推进：活干完了就把阶段标完成（不然会一直卡在早上浇水）。"""
        snap = snap or self.current
        facts = self.facts(snap)
        cur = self.current_stage(snap)
        if not cur:
            return None
        text, name = cur["text"], cur["slot_name"]
        done = False
        if "浇" in text and facts.get("crops_thirsty", 0) == 0 and facts.get("planted_unwatered", 0) == 0:
            done = True
        elif "买" in text and facts.get("seeds", 0) > 0:
            done = True
        elif "种" in text and facts.get("seeds", 0) == 0:
            done = True          # 种子用完了，这条阶段自然结束
        elif "收" in text and facts.get("harvestable", 0) == 0:
            done = True
        elif "清障" in text and facts.get("obstacles_known", 0) == 0:
            done = True
        if done:
            self.advance_schedule(snap)
            return (text, name)
        return None

    def validate_schedule(self, snap=None):
        """按事实把阶段的 blocked/pending 调准。"""
        facts = self.facts(snap)
        scope, today = save_key(snap or self.current), self.game_day(snap or self.current)
        changed = []
        for st in self.schedule(snap):
            if st["status"] == "done":
                continue
            ok, why = self._check_requires(st["requires"], facts)
            want = "pending" if ok else "blocked"
            if st["status"] != want:
                with self.db:
                    self.db.execute("UPDATE schedule SET status=?, updated_ts=? "
                                    "WHERE save_id=? AND day=? AND slot=?",
                                    (want, self.clock(), scope, today, st["slot"]))
                changed.append((st["text"], want, why))
        return changed

    # ---------- 状态板（facts）与任务板（tasks） ----------
    SEED_KEYWORDS = ("种子", "种子包", "Seeds", "seeds")

    def sync_facts(self, snap):
        """从快照派生当前状态，只写变化；返回 {key: (old, new)}。"""
        if not snap:
            return {}
        scope = save_key(snap)
        inv = snap.get("inventory") or []
        ents = snap.get("entities") or []
        seeds = 0
        for it in inv:
            name = it.get("name") or ""
            if any(k in name for k in self.SEED_KEYWORDS):
                cnt = it.get("count")
                try:
                    cnt = 1 if cnt is None else int(cnt)
                except Exception:
                    cnt = 1
                if cnt > 0:          # count=0 表示这一格是空的，别当成 1
                    seeds += cnt
        tilled_empty = sum(1 for e in ents if e.get("kind") == "soil" and e.get("tilled") and not e.get("crop"))
        tillable = sum(1 for e in ents if e.get("diggable") and not e.get("tilled") and not e.get("crop"))
        dry = sum(1 for e in ents if e.get("kind") == "soil" and not e.get("watered")
                  and (e.get("crop") or e.get("tilled")))
        ready = sum(1 for e in ents if e.get("crop") and (e.get("ready") or e.get("harvestable")))
        p = snap.get("player") or {}
        max_items = int(p.get("maxItems") or 12)
        used = sum(1 for i in inv if (i.get("count") or 0) > 0)
        try:
            self.update_phases(snap)          # 视野里看到作物就刷新阶段
        except Exception:
            pass
        try:
            self.update_landmarks(snap)       # 障碍/水源/箱子进地标表
        except Exception:
            pass
        try:
            unwatered = len(self.unwatered_planted(snap))
        except Exception:
            unwatered = 0
        try:
            progress = self.crop_progress(snap)
        except Exception:
            progress = []
        growing = [c for c in progress if c["phase"] < 4]
        overdue = [c for c in growing if c["overdue"]]
        thirsty = [c for c in growing if c["thirsty"]]
        tomorrow = [c for c in growing if c["left"] <= 1 and not c["overdue"]]
        facts = {
            "planted_unwatered": unwatered,
            "crops_growing": len(growing),
            "crops_ready_tomorrow": len(tomorrow),
            "crops_overdue": len(overdue),
            "crops_thirsty": len(thirsty),
            "crops_seen": len(progress),
            "obstacles_known": len(self.landmarks(snap, kind="obstacle")),
            "water_known": len(self.landmarks(snap, kind="water")),
            "chests_known": len(self.landmarks(snap, kind="chest")),
            "seeds": seeds,
            "tilled_empty": tilled_empty,
            "tillable": tillable,
            "crops_dry": dry,
            "harvestable": ready,
            "energy": int(float(p.get("energy") or 0)),
            "inventory_free": max(0, max_items - used),
            "time": int(snap.get("time") or 0),
            "location": str(snap.get("location") or ""),
        }
        changed = {}
        for key, value in facts.items():
            old = self.db.execute("SELECT value FROM facts WHERE save_id=? AND key=?", (scope, key)).fetchone()
            old_v = None if old is None else old[0]
            new_v = str(value)
            if old_v == new_v:
                continue
            changed[key] = (old_v, new_v)
            with self.db:
                self.db.execute("INSERT INTO facts VALUES(?,?,?,?,?) "
                                "ON CONFLICT(save_id,key) DO UPDATE SET value=excluded.value, "
                                "updated_ts=excluded.updated_ts, source=excluded.source",
                                (scope, key, new_v, self.clock(), "derived"))
        if "seeds" in changed and changed["seeds"][1] == "0":
            old_n = changed["seeds"][0]
            self.append("resource", snap, source="game", confidence="observed",
                        data={"text": "种子用完了（seeds=0，之前 %s）" % old_n, "seeds": 0})
        return changed

    def facts(self, snap=None):
        scope = save_key(snap or self.current)
        out = {}
        for row in self.db.execute("SELECT key,value FROM facts WHERE save_id=?", (scope,)):
            try:
                out[row["key"]] = json.loads(row["value"])
            except Exception:
                out[row["key"]] = row["value"]
        return out

    def add_task(self, text, requires=None, snap=None):
        import uuid as _uuid
        scope = save_key(snap or self.current)
        dup = self.db.execute("SELECT id FROM tasks WHERE save_id=? AND text=? AND status!='done'",
                              (scope, str(text)[:300])).fetchone()
        if dup:
            return dup[0]                     # 同一任务不重复登记
        tid = _uuid.uuid4().hex[:12]
        with self.db:
            self.db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?)",
                            (scope, tid, str(text)[:300], "pending",
                             json.dumps(requires or {}, ensure_ascii=False), "", self.clock()))
        return tid

    def tasks(self, snap=None, status=None):
        scope = save_key(snap or self.current)
        sql = "SELECT id,text,status,requires,blocked_reason FROM tasks WHERE save_id=?"
        args = [scope]
        if status:
            sql += " AND status=?"
            args.append(status)
        out = []
        for r in self.db.execute(sql + " ORDER BY updated_ts DESC", args):
            try:
                req = json.loads(r["requires"] or "{}")
            except Exception:
                req = {}
            out.append({"id": r["id"], "text": r["text"], "status": r["status"],
                        "requires": req, "reason": r["blocked_reason"]})
        return out

    def validate_tasks(self, snap=None):
        """按当前事实校验任务前置条件；不满足就 blocked，恢复则解锁。"""
        scope = save_key(snap or self.current)
        facts = self.facts(snap)
        changed = []
        for task in self.tasks(snap):
            if task["status"] == "done":
                continue
            ok, why = self._check_requires(task["requires"], facts)
            want = "pending" if ok else "blocked"
            if task["status"] != want or (not ok and task["reason"] != why):
                with self.db:
                    self.db.execute("UPDATE tasks SET status=?, blocked_reason=?, updated_ts=? "
                                    "WHERE save_id=? AND id=?", (want, why, self.clock(), scope, task["id"]))
                changed.append((task["text"], want, why))
        return changed

    @staticmethod
    def _check_requires(requires, facts):
        """requires 形如 {"seeds": ">=1"}；返回 (是否满足, 原因)。"""
        for key, expr in (requires or {}).items():
            have = facts.get(key)
            if have is None:
                return False, "状态未知：%s" % key
            try:
                if isinstance(expr, str) and expr[:2] in (">=", "<=", "==", "!=", ">", "<"):
                    op, num = expr[:2] if expr[1] == "=" else expr[:1], float(expr.lstrip("><=! "))
                    cur = float(have)
                    want = {">=": cur >= num, "<=": cur <= num, "==": cur == num,
                            "!=": cur != num, ">": cur > num, "<": cur < num}[op]
                else:
                    want = float(have) >= float(expr)
            except Exception:
                return False, "条件无法判断：%s %s" % (key, expr)
            if not want:
                label = {"seeds": "种子", "crops_dry": "待浇地", "harvestable": "成熟作物"}.get(key, key)
                return False, "%s不足（%s=%s，要求%s）" % (label, key, have, expr)
        return True, ""

    # 这些词说明目标是"去弄到种子"，不该被 seeds 条件拦住（否则死锁）
    ACQUIRE_WORDS = ("买", "购买", "获取", "得到", "找", "拿", "取回", "商店", "皮埃尔", "箱子")

    def prune_tasks(self, keep=20, snap=None):
        """任务板不能无限长：只保留最近 keep 条未完成的。"""
        scope = save_key(snap or self.current)
        rows = self.db.execute(
            "SELECT id FROM tasks WHERE save_id=? AND status!='done' ORDER BY updated_ts DESC", (scope,)).fetchall()
        stale = [r[0] for r in rows[keep:]]
        if stale:
            marks = ",".join("?" for _ in stale)
            with self.db:
                self.db.execute("DELETE FROM tasks WHERE save_id=? AND id IN (%s)" % marks,
                                [scope] + stale)
        return len(stale)

    def sync_tasks_from_goal(self, goal, snap=None):
        """按目标语义挂前置条件：播种要 seeds>=1，浇水要 crops_dry>=1，买种子不要条件。"""
        goal = str(goal or "")
        if not goal:
            return None
        acquiring = any(k in goal for k in self.ACQUIRE_WORDS)
        import re as _re
        # "种"但不是"种子"才算播种（"买种子/找种子"已经被 acquiring 排除）
        planting = (not acquiring) and ("播" in goal or "sowing" in goal or "plant" in goal
                                        or bool(_re.search(r"种(?!子)", goal)))
        watering = (not acquiring) and ("浇" in goal)
        harvesting = (not acquiring) and any(k in goal for k in ("收", "采集", "harvest"))
        if harvesting and "harvestable" not in (requires or {}):
            requires["harvestable"] = ">=1" if not requires else requires.get("harvestable", ">=1")
        if not (planting or watering or harvesting):
            return None
        if harvesting:
            requires.setdefault("harvestable", ">=1")
        requires = {}
        if planting:
            requires["seeds"] = ">=1"
        if watering:
            requires["crops_dry"] = ">=1"
        existing = {t["text"] for t in self.tasks(snap)}
        tid = None
        if goal not in existing:
            tid = self.add_task(goal, requires=requires, snap=snap)
        if planting:
            pair = "给新种下的作物浇水"
            if pair not in existing:
                self.add_task(pair, requires={"planted_unwatered": ">=1"}, snap=snap)
        return tid

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
        try:
            facts = self.facts(snap)
        except Exception:
            facts = {}
        try:
            open_tasks = [t for t in self.tasks(snap) if t["status"] != "done"]
        except Exception:
            open_tasks = []
        return {"save_id": scope, "persistent_identity": snap.get("saveId") is not None,
                "state_facts": facts, "open_tasks": open_tasks,
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
