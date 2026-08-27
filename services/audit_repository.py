"""SQLite audit history. A run and its snapshots commit together and never update."""
import csv
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from uuid import uuid4
from services.audit_models import AUDIT_STATES, SOURCE_KINDS, POLICY_VERSION, AuditRun, BuddySnapshot, ReactionObservation, fingerprint, now_kst, canonical_blog_id

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "audit.sqlite3"


class AuditRepository:
    def __init__(self, db_path=None):
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS audit_runs (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL UNIQUE,
                    generated_at TEXT NOT NULL,
                    blog_id TEXT NOT NULL,
                    audit_state TEXT NOT NULL CHECK(audit_state IN ('complete','partial','failed','cancelled')),
                    source_kind TEXT NOT NULL CHECK(source_kind IN ('live','legacy_unverified','fixture')),
                    policy_version TEXT NOT NULL,
                    report_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS buddy_snapshots (
                    run_id TEXT NOT NULL REFERENCES audit_runs(run_id),
                    blog_id TEXT NOT NULL, snapshot_json TEXT NOT NULL,
                    PRIMARY KEY(run_id, blog_id)
                );
                CREATE TABLE IF NOT EXISTS reaction_observations (
                    run_id TEXT NOT NULL REFERENCES audit_runs(run_id),
                    log_no TEXT NOT NULL, blog_id TEXT NOT NULL, observation_json TEXT NOT NULL,
                    PRIMARY KEY(run_id, log_no, blog_id)
                );
                CREATE TABLE IF NOT EXISTS audit_legacy_imports (
                    content_key TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES audit_runs(run_id)
                );
                CREATE TRIGGER IF NOT EXISTS audit_runs_immutable BEFORE UPDATE ON audit_runs
                    BEGIN SELECT RAISE(ABORT, 'audit runs are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS buddy_snapshots_immutable BEFORE UPDATE ON buddy_snapshots
                    BEGIN SELECT RAISE(ABORT, 'buddy snapshots are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS reaction_observations_immutable BEFORE UPDATE ON reaction_observations
                    BEGIN SELECT RAISE(ABORT, 'reaction observations are immutable'); END;
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(str(self.db_path), timeout=15)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=15000")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _encode(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)

    def _save(self, db, report):
        saved = json.loads(self._encode(report))
        saved.setdefault("run_id", str(uuid4()))
        saved.setdefault("generated_at", now_kst().isoformat())
        saved.setdefault("policy_version", POLICY_VERSION)
        saved.setdefault("source_kind", "legacy_unverified")
        saved.setdefault("audit_state", "partial")
        saved.setdefault("blog_id", "")
        saved.setdefault("quality_issues", [])
        if saved["audit_state"] not in AUDIT_STATES or saved["source_kind"] not in SOURCE_KINDS:
            raise ValueError("invalid audit state or source kind")
        if not isinstance(saved["run_id"], str) or not saved["run_id"]:
            raise ValueError("run_id required")
        saved["comparison_eligible"] = (saved["source_kind"] == "live" and saved["audit_state"] == "complete"
                                        and saved.get("capability_verified") is True and bool(saved.get("posts")))
        run = AuditRun(**{key: saved[key] for key in AuditRun.__dataclass_fields__})
        try:
            db.execute("INSERT INTO audit_runs(run_id,generated_at,blog_id,audit_state,source_kind,policy_version,report_json) VALUES(?,?,?,?,?,?,?)",
                       (run.run_id, run.generated_at, run.blog_id, run.audit_state, run.source_kind, run.policy_version, self._encode(saved)))
            for row in saved.get("master_buddies", []):
                snapshot = BuddySnapshot(run.run_id, row["blog_id"], row.get("added_date"), row.get("new_posts_setting", "unknown"), row.get("setting_observed_at"))
                db.execute("INSERT INTO buddy_snapshots VALUES(?,?,?)", (snapshot.run_id, snapshot.blog_id, self._encode(row)))
            for row in saved.get("reaction_observations", []):
                observation = ReactionObservation(run.run_id, str(row["log_no"]), row["blog_id"], row.get("liked"), row.get("commented"), row.get("comment_entry_count"))
                db.execute("INSERT INTO reaction_observations VALUES(?,?,?,?)", (observation.run_id, observation.log_no, observation.blog_id, self._encode(row)))
        except sqlite3.IntegrityError as error:
            raise ValueError("duplicate run or snapshot identity") from error
        return run.run_id

    def save_run(self, report):
        with self._connect() as db:
            return self._save(db, report)

    def get_run(self, run_id):
        with self._connect() as db:
            row = db.execute("SELECT report_json FROM audit_runs WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_runs(self):
        with self._connect() as db:
            rows = db.execute("SELECT report_json FROM audit_runs ORDER BY sequence DESC").fetchall()
        return [json.loads(row[0]) for row in rows]

    def latest_run(self):
        with self._connect() as db:
            row = db.execute("SELECT report_json FROM audit_runs ORDER BY sequence DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else None

    def import_legacy(self, directory):
        """Import only known audit filenames. Source files remain untouched."""
        directory = Path(directory)
        paths = sorted(directory.glob("my_blog_engagement_audit*.json")) + sorted(directory.glob("buddy_engagement_audit*.csv"))
        imported = []
        for path in paths:
            if not path.is_file() or path.is_symlink(): continue
            raw = path.read_bytes()
            content_key = fingerprint({"name": path.name, "content": raw.hex()})
            with self._connect() as db:
                if db.execute("SELECT 1 FROM audit_legacy_imports WHERE content_key=?", (content_key,)).fetchone(): continue
                try:
                    if path.suffix == ".json":
                        source = json.loads(raw.decode("utf-8-sig"))
                        if not isinstance(source, dict): raise ValueError("not a report")
                    else:
                        import io
                        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
                        source = {"master_buddies": [{"blog_id": row.get("블로그ID", row.get("blog_id", f"unknown-{index}")),
                                                            "nickname": row.get("닉네임", ""), "legacy_fields": row,
                                                            "reaction_status": "과거자료 미검증", "no_reaction": None}
                                                           for index, row in enumerate(rows)]}
                except (ValueError, UnicodeError, csv.Error):
                    continue
                original = json.loads(self._encode(source))
                source.pop("run_id", None)
                source.update(source_kind="legacy_unverified", audit_state="partial", capability_verified=False,
                              comparison_eligible=False, legacy_source_name=path.name,
                              quality_issues=["legacy_evidence_unverified"], policy_version="legacy_unverified")
                source.setdefault("master_buddies", [])
                source.setdefault("posts", [])
                source.setdefault("non_buddy_reactors", [])
                source["unresponsive_buddies"] = []
                source['legacy_original'] = original
                source['excluded_evidence'] = []
                # All old claims remain in provenance; invalid routes never become people.
                for key in ('master_buddies', 'non_buddy_reactors'):
                    valid = []
                    for row in source[key]:
                        if not canonical_blog_id(row.get('blog_id')):
                            source['excluded_evidence'].append({'reason': 'invalid_profile_or_menu_route', 'row': row})
                            continue
                        row["legacy_no_reaction"] = row.get("no_reaction")
                        row["no_reaction"] = None
                        row["reaction_status"] = "과거자료 미검증"
                        row['scan_complete'] = False
                        valid.append(row)
                    source[key] = valid
                for key in ('unresponsive_buddies_count', 'real_unresponsive_count', 'reacted_buddies_count'):
                    source[key] = None
                if source['excluded_evidence']:
                    source['quality_issues'].append('legacy_invalid_participants_excluded')
                if path.name == 'buddy_engagement_audit_20260826.csv' and {r['blog_id'] for r in source['master_buddies']} == {'b1', 'b2'}:
                    source['quality_issues'].append('legacy_fixture_suspected')
                    for row in source['master_buddies']: row['reaction_status'] = '테스트 자료 의심 · 미검증'
                run_id = self._save(db, source)
                db.execute("INSERT INTO audit_legacy_imports VALUES(?,?)", (content_key, run_id))
                imported.append(run_id)
        return imported

    def compare_runs(self, previous_id, current_id):
        previous, current = self.get_run(previous_id), self.get_run(current_id)
        reasons = []
        if not previous or not current:
            return {"comparable": False, "reasons": ["run_not_found"], "delta": None}
        if not previous.get("comparison_eligible") or not current.get("comparison_eligible"):
            reasons.append("unverified_or_incomplete_source")
        for key in ("blog_id", "policy_version"):
            if previous.get(key) != current.get(key): reasons.append(key + "_mismatch")
        post_key = lambda report: sorted((str(p.get("log_no")), p.get("published_at"), p.get("published_at_precision")) for p in report.get("posts", []))
        cohort_key = lambda report: sorted(row["blog_id"] for row in report.get("master_buddies", []))
        if post_key(previous) != post_key(current): reasons.append("post_window_mismatch")
        if cohort_key(previous) != cohort_key(current): reasons.append("cohort_mismatch")
        if reasons: return {"comparable": False, "reasons": reasons, "delta": None}
        old = {row["blog_id"]: row for row in previous["master_buddies"]}
        delta = []
        for row in current["master_buddies"]:
            changes = {"blog_id": row["blog_id"]}
            for key in ("like_count", "comment_count", "comment_entry_count", "engaged_post_count"):
                before, after = old[row["blog_id"]].get(key), row.get(key)
                if not isinstance(before, int) or not isinstance(after, int):
                    return {"comparable": False, "reasons": ["unknown_observations"], "delta": None}
                changes[key] = after - before
            delta.append(changes)
        return {"comparable": True, "reasons": [], "delta": delta}
