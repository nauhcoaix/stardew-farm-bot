"""经验库：不可变证据事件 + 可检索经验 + 技能版本 + 按存档隔离的地图索引。

职责边界：

* 这里保存**证据**和**验证状态**，不把“模型说过”升级成事实；
* 存档事实（地图、库存、关系）按 ``save_key`` 隔离；验证过的机制经验可携带证据跨存档迁移，
  但在新环境仍须检查前提；
* 组合技能候选与已验证版本分开保存，晋级规则见 :mod:`verdict`；
* 不负责调度、不控制游戏、不执行动作。

存储后端为 SQLite（单文件），因为需要事务、索引和跨进程读取；对外只暴露公开函数，
调用方不读取表结构。
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..contracts.errors import ExperienceError
from ..contracts.experience import (
    ExperienceRecord,
    Event,
    Premise,
    SkillRun,
    SkillVersion,
)
from ..contracts.results import ActionResult
from .skills import validate_skill
from .verdict import PromotionDecision, evaluate_promotion

SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  id TEXT PRIMARY KEY, save_key TEXT, session TEXT, ts REAL, kind TEXT, status TEXT,
  ability TEXT, source TEXT, confidence TEXT, body TEXT);
CREATE INDEX IF NOT EXISTS events_recall ON events(save_key, kind, ts DESC);

CREATE TABLE IF NOT EXISTS experiences(
  id TEXT PRIMARY KEY, save_key TEXT, scope TEXT, kind TEXT, claim TEXT, premises TEXT,
  source TEXT, confidence TEXT, status TEXT, version INTEGER, created_ts REAL, updated_ts REAL);
CREATE INDEX IF NOT EXISTS experiences_scope ON experiences(scope, save_key, status);

CREATE TABLE IF NOT EXISTS experience_evidence(
  experience_id TEXT, event_id TEXT, role TEXT, PRIMARY KEY(experience_id, event_id, role));

CREATE TABLE IF NOT EXISTS experience_grams(gram TEXT, experience_id TEXT, save_key TEXT);
CREATE INDEX IF NOT EXISTS experience_grams_lookup ON experience_grams(gram, save_key);

CREATE TABLE IF NOT EXISTS experience_vectors(experience_id TEXT PRIMARY KEY, dim INTEGER, blob BLOB);

CREATE TABLE IF NOT EXISTS skills(
  id TEXT, version INTEGER, status TEXT, definition TEXT, created_ts REAL, updated_ts REAL,
  retired_reason TEXT, PRIMARY KEY(id, version));

CREATE TABLE IF NOT EXISTS skill_runs(
  id TEXT PRIMARY KEY, skill_id TEXT, skill_version INTEGER, event_id TEXT, outcome TEXT,
  premises_key TEXT, cancelled_cleanly INTEGER, ts REAL);
CREATE INDEX IF NOT EXISTS skill_runs_lookup ON skill_runs(skill_id, skill_version);

CREATE TABLE IF NOT EXISTS tiles(
  save_key TEXT, location TEXT, x INTEGER, y INTEGER, passable INTEGER, visited INTEGER, ts REAL,
  PRIMARY KEY(save_key, location, x, y));

CREATE TABLE IF NOT EXISTS links(
  save_key TEXT, origin TEXT, destination TEXT, target TEXT, confirmed INTEGER, ts REAL,
  PRIMARY KEY(save_key, origin, destination, target));

CREATE TABLE IF NOT EXISTS facts(
  save_key TEXT, key TEXT, value TEXT, source TEXT, ts REAL, PRIMARY KEY(save_key, key));
PRAGMA user_version=1;
"""


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def grams(text: str, size: int = 2) -> list[str]:
    """简易 2-gram 索引（中文按字符切分），供无语义模型时的关键词检索。"""
    cleaned = "".join(ch.lower() for ch in str(text) if not ch.isspace())
    if len(cleaned) < size:
        return [cleaned] if cleaned else []
    return [cleaned[index : index + size] for index in range(len(cleaned) - size + 1)]


class ExperienceStore:
    """经验与证据的唯一拥有者。"""

    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        clock=time.time,
        encoder: Any | None = None,
    ) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.encoder = encoder
        self.db = sqlite3.connect(self.path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ---- 事件 -----------------------------------------------------
    def append_event(self, event: Event) -> str:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO events VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    event.id,
                    event.save_key,
                    event.session,
                    event.recorded_at,
                    event.kind,
                    event.status,
                    event.ability,
                    event.source,
                    event.confidence,
                    _dumps(event.to_dict()),
                ),
            )
        return event.id

    def record_action(
        self,
        result: ActionResult,
        *,
        save_key: str,
        session: str = "",
        goal: str = "",
        decision: Mapping[str, Any] | None = None,
        source: str = "executor",
    ) -> str:
        """把一次委托结果写成证据事件（结果未知也照实记录）。"""
        event = Event(
            id="ev:" + uuid.uuid4().hex,
            kind="action",
            recorded_at=self.clock(),
            save_key=save_key,
            session=session,
            ability=result.ability,
            status=result.state,
            source=source,
            confidence="execution",
            payload={"goal": goal, "result": result.to_dict(), "decision": dict(decision or {})},
        )
        return self.append_event(event)

    def event(self, event_id: str) -> dict | None:
        row = self.db.execute("SELECT body FROM events WHERE id=?", (event_id,)).fetchone()
        return _loads(row["body"], None) if row else None

    def events_for_save(self, save_key: str, *, kind: str | None = None, limit: int = 50) -> list[dict]:
        if kind:
            rows = self.db.execute(
                "SELECT body FROM events WHERE save_key=? AND kind=? ORDER BY ts DESC LIMIT ?",
                (save_key, kind, int(limit)),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT body FROM events WHERE save_key=? ORDER BY ts DESC LIMIT ?", (save_key, int(limit))
            ).fetchall()
        return [_loads(row["body"], {}) for row in rows]

    def recent_actions(self, save_key: str, *, limit: int = 8) -> list[dict]:
        """该存档最近的动作结果摘要（最新在前）。

        用于重启后第一轮决策：内存里的运行时历史为空，从证据库补读，
        让模型仍能看到之前哪些动作失败/未知/被拦截。
        """
        rows = self.db.execute(
            "SELECT body FROM events WHERE save_key=? AND kind='action' ORDER BY ts DESC LIMIT ?",
            (save_key, int(limit)),
        ).fetchall()
        briefs: list[dict] = []
        for row in rows:
            body = _loads(row["body"], {})
            payload = body.get("payload") or {}
            raw = payload.get("result") or {}
            briefs.append(
                {
                    "ability": raw.get("ability") or body.get("ability") or "?",
                    "params": raw.get("params") or {},
                    "state": raw.get("state") or body.get("status") or "unknown",
                    "detail": raw.get("detail") or "",
                    "progress": bool(raw.get("progress")),
                    "error": raw.get("error"),
                    "goal": payload.get("goal") or "",
                    "recorded_at": body.get("recorded_at"),
                }
            )
        return briefs

    # ---- 经验 -----------------------------------------------------
    def add_experience(
        self,
        claim: str,
        *,
        kind: str = "mechanism",
        premises: Sequence[Premise | Mapping[str, Any]] = (),
        source: str = "executor",
        confidence: str = "execution",
        scope: str = "mechanism",
        scope_save: str | None = None,
        evidence_ids: Sequence[str] = (),
        counterexample_ids: Sequence[str] = (),
        status: str = "candidate",
    ) -> ExperienceRecord:
        """写入一条候选经验。总结/相似度/模型自信都不能在这里升级状态。"""
        normalized = [
            row if isinstance(row, Premise) else Premise.from_dict(row) for row in premises
        ]
        now = self.clock()
        if scope == "save" and not scope_save:
            raise ExperienceError("存档经验必须提供 scope_save，避免跨存档混用")
        if status == "verified" and not evidence_ids and not counterexample_ids:
            # 模型总结、相似度、自信都不能把一条没有证据的文字升级成“已验证”。
            raise ExperienceError("verified 经验必须附证据 event_id；不接受只靠文字或 confidence 升级")
        record = ExperienceRecord(
            id="exp:" + uuid.uuid4().hex,
            claim=str(claim),
            kind=str(kind),
            premises=tuple(normalized),
            source=source,
            confidence=confidence,
            evidence_ids=tuple(evidence_ids),
            counterexample_ids=tuple(counterexample_ids),
            status=status,
            scope=scope,
            scope_save=scope_save if scope == "save" else None,
            created_at=now,
            updated_at=now,
        )
        with self.db:
            self.db.execute(
                "INSERT INTO experiences VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.id,
                    record.scope_save,
                    record.scope,
                    record.kind,
                    record.claim,
                    _dumps([premise.to_dict() for premise in record.premises]),
                    record.source,
                    record.confidence,
                    record.status,
                    record.version,
                    record.created_at,
                    record.updated_at,
                ),
            )
            for event_id in record.evidence_ids:
                self.db.execute(
                    "INSERT OR IGNORE INTO experience_evidence VALUES(?,?,?)", (record.id, event_id, "support")
                )
            for event_id in record.counterexample_ids:
                self.db.execute(
                    "INSERT OR IGNORE INTO experience_evidence VALUES(?,?,?)", (record.id, event_id, "counter")
                )
            self._index_experience(record)
        return record

    def add_evidence(self, experience_id: str, event_id: str, *, counterexample: bool = False) -> None:
        if self.experience(experience_id) is None:
            raise ExperienceError("经验不存在：%s" % experience_id)
        role = "counter" if counterexample else "support"
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO experience_evidence VALUES(?,?,?)", (experience_id, event_id, role))
            self.db.execute(
                "UPDATE experiences SET updated_ts=?, version=version+1 WHERE id=?", (self.clock(), experience_id)
            )

    def set_experience_status(self, experience_id: str, status: str, *, note: str = "") -> None:
        """人工/证据驱动的状态变更；``retired`` 表示失效并保留历史。"""
        if status not in ("candidate", "verified", "retired"):
            raise ExperienceError("未知经验状态：%s" % status)
        with self.db:
            self.db.execute(
                "UPDATE experiences SET status=?, updated_ts=?, version=version+1 WHERE id=?",
                (status, self.clock(), experience_id),
            )
            if note:
                self.db.execute(
                    "INSERT OR IGNORE INTO experience_evidence VALUES(?,?,?)",
                    (experience_id, "note:" + note[:120], "note"),
                )

    def experience(self, experience_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM experiences WHERE id=?", (experience_id,)).fetchone()
        return self._experience_row(row) if row else None

    def evidence_counts(self, experience_id: str) -> dict:
        rows = self.db.execute(
            "SELECT role, COUNT(*) AS n FROM experience_evidence WHERE experience_id=? GROUP BY role", (experience_id,)
        ).fetchall()
        counts = {"support": 0, "counter": 0}
        for row in rows:
            if row["role"] in counts:
                counts[row["role"]] = int(row["n"])
        return counts

    def recall(
        self,
        query: str,
        *,
        save_key: str,
        limit: int = 8,
        include_candidates: bool = True,
        include_retired: bool = False,
    ) -> list[dict]:
        """检索经验：本存档事实 + 已验证的机制经验；候选默认也返回但带状态。

        排序依据是关键词/向量重合 + 验证状态 + 新旧，不会因为“相似”就提升验证等级。
        """
        statuses = ["verified"]
        if include_candidates:
            statuses.append("candidate")
        if include_retired:
            statuses.append("retired")
        placeholders = ",".join("?" for _ in statuses)
        rows = self.db.execute(
            "SELECT * FROM experiences WHERE status IN (%s) AND (scope='mechanism' OR (scope='save' AND save_key=?))"
            % placeholders,
            (*statuses, save_key),
        ).fetchall()
        qgrams = set(grams(query))
        qvec = self._encode(query)
        scored: list[tuple[float, dict]] = []
        for row in rows:
            record = self._experience_row(row)
            overlap = self._overlap(record["id"], qgrams)
            similarity = self._similarity(record["id"], qvec)
            score = overlap + similarity
            if record["status"] == "verified":
                score += 0.5
            if record["scope"] == "save":
                score += 0.1
            scored.append((score, record))
        if any(score > 0 for score, _ in scored):
            scored = [item for item in scored if item[0] > 0]
        scored.sort(key=lambda item: (item[0], item[1]["updated_at"]), reverse=True)
        results = []
        for score, record in scored[:limit]:
            results.append({**record, "evidence": self.evidence_counts(record["id"]), "score": round(score, 3)})
        return results

    def _experience_row(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "claim": row["claim"],
            "kind": row["kind"],
            "premises": _loads(row["premises"], []),
            "source": row["source"],
            "confidence": row["confidence"],
            "status": row["status"],
            "scope": row["scope"],
            "scope_save": row["save_key"],
            "version": int(row["version"] or 1),
            "created_at": row["created_ts"],
            "updated_at": row["updated_ts"],
        }

    def _index_experience(self, record: ExperienceRecord) -> None:
        save_key = record.scope_save or ""
        for gram in set(grams(record.claim)):
            self.db.execute(
                "INSERT INTO experience_grams VALUES(?,?,?)", (gram, record.id, save_key)
            )
        vector = self._encode(record.claim)
        if vector:
            from ..embeddings import to_blob

            self.db.execute(
                "INSERT OR REPLACE INTO experience_vectors VALUES(?,?,?)", (record.id, len(vector), to_blob(vector))
            )

    def _overlap(self, experience_id: str, qgrams: set[str]) -> float:
        if not qgrams:
            return 0.0
        placeholders = ",".join("?" for _ in qgrams)
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM experience_grams WHERE experience_id=? AND gram IN (%s)" % placeholders,
            (experience_id, *qgrams),
        ).fetchone()
        return min(1.0, float(row["n"]) / max(1.0, len(qgrams)))

    def _encode(self, text: str) -> list[float] | None:
        encoder = self.encoder
        if encoder is None:
            return None
        try:
            return encoder.encode_one(text)
        except Exception:  # noqa: BLE001 - 向量不可用不能影响检索主路径
            return None

    def _similarity(self, experience_id: str, query: list[float] | None) -> float:
        if not query:
            return 0.0
        row = self.db.execute("SELECT blob FROM experience_vectors WHERE experience_id=?", (experience_id,)).fetchone()
        if not row:
            return 0.0
        from ..embeddings import cosine, from_blob

        return max(0.0, cosine(query, [from_blob(row["blob"])])[0])

    # ---- 组合技能 -------------------------------------------------
    def experience_by_claim(self, claim: str, *, scope_save: str) -> dict | None:
        """按“主张 + 存档”找已有经验（用于同一动作的重复证据合并）。"""
        row = self.db.execute(
            "SELECT * FROM experiences WHERE claim=? AND scope='save' AND save_key=? "
            "ORDER BY updated_ts DESC LIMIT 1",
            (str(claim), str(scope_save)),
        ).fetchone()
        return self._experience_row(row) if row else None

    def experience_evidence_ids(self, experience_id: str, *, role: str = "support") -> list[str]:
        """取某条经验的支持/反例事件 id（技能运行记录需要真实事件）。"""
        rows = self.db.execute(
            "SELECT event_id FROM experience_evidence WHERE experience_id=? AND role=? ORDER BY rowid",
            (experience_id, role),
        ).fetchall()
        return [str(row["event_id"]) for row in rows]

    def add_skill(
        self,
        definition: Mapping[str, Any],
        *,
        ability_versions: Mapping[str, str] | None = None,
        status: str = "candidate",
    ) -> SkillVersion:
        """保存一份技能定义的新版本；旧版本原样保留。"""
        normalized = validate_skill(definition, ability_versions=ability_versions)
        row = self.db.execute("SELECT MAX(version) AS v FROM skills WHERE id=?", (normalized["id"],)).fetchone()
        version = int(row["v"] or 0) + 1
        now = self.clock()
        record = SkillVersion(id=normalized["id"], version=version, definition=normalized, status=status, created_at=now)
        with self.db:
            self.db.execute(
                "INSERT INTO skills VALUES(?,?,?,?,?,?,?)",
                (record.id, version, status, _dumps(normalized), now, now, None),
            )
        return record

    def record_skill_run(
        self,
        skill_id: str,
        version: int,
        event_id: str,
        outcome: str,
        *,
        premises_key: str = "",
        cancelled_cleanly: bool = False,
    ) -> SkillRun:
        run = SkillRun(
            skill_id=skill_id,
            skill_version=int(version),
            event_id=event_id,
            outcome=outcome,
            premises_key=premises_key,
            cancelled_cleanly=bool(cancelled_cleanly),
            recorded_at=self.clock(),
        )
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO skill_runs VALUES(?,?,?,?,?,?,?,?)",
                (
                    "run:" + uuid.uuid4().hex,
                    run.skill_id,
                    run.skill_version,
                    run.event_id,
                    run.outcome,
                    run.premises_key,
                    1 if run.cancelled_cleanly else 0,
                    run.recorded_at,
                ),
            )
        return run

    def skill_runs(self, skill_id: str, version: int | None = None) -> list[SkillRun]:
        if version is None:
            version = self.latest_skill_version(skill_id)
        if version is None:
            return []
        rows = self.db.execute(
            "SELECT * FROM skill_runs WHERE skill_id=? AND skill_version=?", (skill_id, int(version))
        ).fetchall()
        return [
            SkillRun(
                skill_id=row["skill_id"],
                skill_version=int(row["skill_version"]),
                event_id=row["event_id"],
                outcome=row["outcome"],
                premises_key=row["premises_key"] or "",
                cancelled_cleanly=bool(row["cancelled_cleanly"]),
                recorded_at=row["ts"],
            )
            for row in rows
        ]

    def latest_skill_version(self, skill_id: str) -> int | None:
        row = self.db.execute("SELECT MAX(version) AS v FROM skills WHERE id=?", (skill_id,)).fetchone()
        return int(row["v"]) if row and row["v"] is not None else None

    def skill(self, skill_id: str, version: int | None = None) -> dict | None:
        if version is None:
            version = self.latest_skill_version(skill_id)
        if version is None:
            return None
        row = self.db.execute("SELECT * FROM skills WHERE id=? AND version=?", (skill_id, int(version))).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "version": int(row["version"]),
            "status": row["status"],
            "definition": _loads(row["definition"], {}),
            "created_at": row["created_ts"],
            "updated_at": row["updated_ts"],
            "retired_reason": row["retired_reason"],
        }

    def promote_skill(self, skill_id: str, *, required_successes: int = 3) -> PromotionDecision:
        """满足晋级条件时把最新版本标记为 verified，并停用更早版本（保留历史）。"""
        version = self.latest_skill_version(skill_id)
        if version is None:
            raise ExperienceError("技能不存在：%s" % skill_id)
        decision = evaluate_promotion(self.skill_runs(skill_id, version), required_successes=required_successes)
        if not decision.promote:
            return decision
        now = self.clock()
        with self.db:
            self.db.execute(
                "UPDATE skills SET status='verified', updated_ts=? WHERE id=? AND version=?", (now, skill_id, version)
            )
            self.db.execute(
                "UPDATE skills SET status='retired', retired_reason=?, updated_ts=? "
                "WHERE id=? AND version<? AND status!='retired'",
                ("被版本 %d 取代" % version, now, skill_id, version),
            )
        return decision

    def retire_skill(self, skill_id: str, reason: str, *, version: int | None = None) -> None:
        if version is None:
            version = self.latest_skill_version(skill_id)
        if version is None:
            raise ExperienceError("技能不存在：%s" % skill_id)
        with self.db:
            self.db.execute(
                "UPDATE skills SET status='retired', retired_reason=?, updated_ts=? WHERE id=? AND version=?",
                (reason, self.clock(), skill_id, version),
            )

    def demote_skill(self, skill_id: str, version: int, reason: str) -> None:
        """满足前提仍失败：退回候选并要求新证据（旧版本与证据保留）。"""
        with self.db:
            self.db.execute(
                "UPDATE skills SET status='candidate', retired_reason=?, updated_ts=? WHERE id=? AND version=?",
                (reason, self.clock(), skill_id, int(version)),
            )

    def active_skills(self) -> list[dict]:
        """当前可复用的已验证技能（每个 id 取最新已验证版本）。"""
        rows = self.db.execute("SELECT * FROM skills WHERE status='verified' ORDER BY id, version DESC").fetchall()
        seen: dict[str, dict] = {}
        for row in rows:
            if row["id"] in seen:
                continue
            seen[row["id"]] = {
                "id": row["id"],
                "version": int(row["version"]),
                "definition": _loads(row["definition"], {}),
                "created_at": row["created_ts"],
            }
        return list(seen.values())

    # ---- 地图索引（按存档隔离） -----------------------------------
    def record_tiles(
        self,
        save_key: str,
        location: str,
        tiles: Iterable[Mapping[str, Any]],
        *,
        mark_visited: bool = False,
    ) -> int:
        now = self.clock()
        written = 0
        with self.db:
            for row in tiles:
                tile = row.get("tile") or {}
                x, y = tile.get("x"), tile.get("y")
                if x is None or y is None or "passable" not in row:
                    continue
                self.db.execute(
                    "INSERT INTO tiles VALUES(?,?,?,?,?,?,?) "
                    "ON CONFLICT(save_key,location,x,y) DO UPDATE SET "
                    "passable=excluded.passable, visited=MAX(tiles.visited, excluded.visited), ts=excluded.ts",
                    (save_key, location, int(x), int(y), 1 if row.get("passable") else 0, 1 if mark_visited else 0, now),
                )
                written += 1
        return written

    def known_tiles(self, save_key: str, location: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT x, y, passable, visited FROM tiles WHERE save_key=? AND location=?", (save_key, location)
        ).fetchall()
        return [
            {
                "tile": {"x": int(row["x"]), "y": int(row["y"])},
                "passable": bool(row["passable"]),
                "visited": bool(row["visited"]),
            }
            for row in rows
        ]

    def record_link(
        self, save_key: str, origin: str, destination: str, target: str = "", *, confirmed: bool = False
    ) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO links VALUES(?,?,?,?,?,?)",
                (save_key, origin, destination, target, 1 if confirmed else 0, self.clock()),
            )

    def links(self, save_key: str) -> list[dict]:
        rows = self.db.execute("SELECT * FROM links WHERE save_key=?", (save_key,)).fetchall()
        return [
            {
                "from": row["origin"],
                "to": row["destination"],
                "target": row["target"],
                "confirmed": bool(row["confirmed"]),
            }
            for row in rows
        ]

    # ---- 存档事实 -------------------------------------------------
    def set_fact(self, save_key: str, key: str, value: Any, *, source: str = "executor") -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO facts VALUES(?,?,?,?,?)",
                (save_key, key, _dumps(value), source, self.clock()),
            )

    def get_fact(self, save_key: str, key: str) -> Any:
        row = self.db.execute("SELECT value FROM facts WHERE save_key=? AND key=?", (save_key, key)).fetchone()
        return _loads(row["value"], None) if row else None

    # ---- 只读汇总 -------------------------------------------------
    def all_events(self, *, limit: int = 100000) -> list[dict]:
        rows = self.db.execute("SELECT body FROM events ORDER BY ts DESC LIMIT ?", (int(limit),)).fetchall()
        return [_loads(row["body"], {}) for row in rows]

    def list_experiences(self, *, save_key: str | None = None, status: str | None = None) -> list[dict]:
        conditions, params = [], []
        if save_key:
            conditions.append("(scope='mechanism' OR (scope='save' AND save_key=?))")
            params.append(save_key)
        if status:
            conditions.append("status=?")
            params.append(status)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = self.db.execute("SELECT * FROM experiences" + where + " ORDER BY updated_ts DESC", tuple(params)).fetchall()
        return [self._experience_row(row) for row in rows]

    def list_skills(self) -> list[dict]:
        rows = self.db.execute("SELECT id, version FROM skills ORDER BY id, version DESC").fetchall()
        return [row for row in (self.skill(row["id"], row["version"]) for row in rows) if row]

    def summary(self) -> dict:
        def count(sql: str, params: tuple = ()) -> int:
            row = self.db.execute(sql, params).fetchone()
            return int(row[0] or 0)

        statuses = self.db.execute("SELECT status, COUNT(*) FROM experiences GROUP BY status").fetchall()
        skill_statuses = self.db.execute("SELECT status, COUNT(*) FROM skills GROUP BY status").fetchall()
        return {
            "events": count("SELECT COUNT(*) FROM events"),
            "experiences": {row[0]: int(row[1]) for row in statuses},
            "skills": {row[0]: int(row[1]) for row in skill_statuses},
            "tiles": count("SELECT COUNT(*) FROM tiles"),
            "links": count("SELECT COUNT(*) FROM links"),
            "saves": count("SELECT COUNT(DISTINCT save_key) FROM tiles"),
        }

    def action_stats(self, *, save_key: str | None = None, since_ts: float | None = None) -> dict:
        """统计所有行动的结果与拦截占比（历史累计，按存档/时间可过滤）。

        口径：

        * ``executed``：真正下发并结算的动作（``kind='action'``）；
        * ``blocked``：未下发就被挡下的动作（限频、前提拒绝、所有权被占用）；
        * ``attempts = executed + blocked``，各类占比以 ``attempts`` 为分母；
        * ``failure_ratio_executed = failed / executed``，单看已执行动作的失败率。
        """
        conditions = [
            "(kind='action' OR kind IN ('repeat_blocked','delegation_rejected','ownership_busy'))"
        ]
        params: list[Any] = []
        if save_key:
            conditions.append("save_key=?")
            params.append(save_key)
        if since_ts is not None:
            conditions.append("ts>=?")
            params.append(float(since_ts))
        rows = self.db.execute(
            "SELECT ability, kind, status, COUNT(*) AS n FROM events WHERE "
            + " AND ".join(conditions)
            + " GROUP BY ability, kind, status",
            tuple(params),
        ).fetchall()

        totals = _empty_action_bucket()
        per_ability: dict[str, dict] = {}
        for row in rows:
            name = row["ability"] or "(unknown)"
            bucket = per_ability.setdefault(name, _empty_action_bucket())
            _add_action_row(bucket, str(row["kind"]), str(row["status"] or ""), int(row["n"]))
            _add_action_row(totals, str(row["kind"]), str(row["status"] or ""), int(row["n"]))

        result = _finish_action_bucket(totals)
        result["filters"] = {"save_key": save_key, "since_ts": since_ts}
        finished = [
            dict(_finish_action_bucket(bucket), ability=name) for name, bucket in per_ability.items()
        ]
        finished = [row for row in finished if row["attempts"]]
        finished.sort(key=lambda row: row["attempts"], reverse=True)
        result["by_ability"] = finished
        return result


RESULT_STATES = ("completed", "failed", "unknown", "cancelled")
BLOCKED_KINDS = {
    "repeat_blocked": "blocked_repeat",
    "delegation_rejected": "blocked_premise",
    "ownership_busy": "blocked_ownership",
}


def _empty_action_bucket() -> dict:
    return {
        "attempts": 0,
        "executed": 0,
        "blocked": 0,
        "completed": 0,
        "failed": 0,
        "unknown": 0,
        "cancelled": 0,
        "blocked_repeat": 0,
        "blocked_premise": 0,
        "blocked_ownership": 0,
    }


def _add_action_row(bucket: dict, kind: str, status: str, count: int) -> None:
    if kind == "action":
        state = status if status in RESULT_STATES else "unknown"
        bucket[state] += count
        bucket["executed"] += count
        return
    bucket["blocked"] += count
    bucket[BLOCKED_KINDS.get(kind, "blocked_premise")] += count


def _finish_action_bucket(bucket: dict) -> dict:
    attempts = bucket["executed"] + bucket["blocked"]
    ratios = {
        name: (round(bucket[name] / attempts, 4) if attempts else 0.0)
        for name in ("completed", "failed", "unknown", "cancelled", "blocked")
    }
    failures = round(bucket["failed"] / bucket["executed"], 4) if bucket["executed"] else 0.0
    return {
        **bucket,
        "attempts": attempts,
        "ratios": ratios,
        "failure_ratio_executed": failures,
        "summary_line": (
            "行动 %d 次：成功 %d (%.1f%%)、失败 %d (%.1f%%)、未知 %d (%.1f%%)、取消 %d (%.1f%%)、拦截 %d (%.1f%%)"
            % (
                attempts,
                bucket["completed"],
                ratios["completed"] * 100,
                bucket["failed"],
                ratios["failed"] * 100,
                bucket["unknown"],
                ratios["unknown"] * 100,
                bucket["cancelled"],
                ratios["cancelled"] * 100,
                bucket["blocked"],
                ratios["blocked"] * 100,
            )
        ),
    }





