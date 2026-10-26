"""Versioned review transactions; source checks and response snapshots precede commit."""

import json
import shutil
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

from .config import UNIT_RULES_VERSION
from .fields import ERROR_MESSAGES, SCHEMA, extract_fields, normalized_observation, parse_quantity, validate
from .models import EvidenceLine, Observation, ProjectError
from .parsing import digest, preflight

JSON_COLUMNS = ("pages", "observations", "fields", "issues", "backend")


def decimal_compare(first, second):
    if first is None or second is None:
        return None
    first, second = Decimal(str(first)), Decimal(str(second))
    return int(first > second) - int(first < second)


class Ledger:
    def __init__(self, settings, parser):
        self.settings, self.parser = settings, parser
        self.mapper = None
        if settings.field_mapper != "rules":
            from .mapping_scope import ScopedFieldMapper

            self.mapper = ScopedFieldMapper(settings.mapping_models, settings.field_mapper)
        settings.data.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, filename TEXT NOT NULL,
                    source TEXT NOT NULL, pdf_path TEXT NOT NULL, pages TEXT NOT NULL,
                    observations TEXT NOT NULL, fields TEXT NOT NULL, issues TEXT NOT NULL,
                    backend TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                    processing_ms REAL NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    rules_version TEXT NOT NULL DEFAULT 'legacy-v1'
                );
                CREATE TABLE IF NOT EXISTS records (
                    document_id TEXT PRIMARY KEY REFERENCES documents(id), model TEXT NOT NULL,
                    power_w REAL NOT NULL, voltage_v REAL NOT NULL, current_a REAL NOT NULL,
                    mass_kg REAL NOT NULL, max_temp_c REAL NOT NULL, ip_rating TEXT NOT NULL,
                    voltage_kind TEXT, current_kind TEXT
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                    revision INTEGER NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS document_versions (
                    document_id TEXT NOT NULL REFERENCES documents(id), revision INTEGER NOT NULL,
                    action TEXT NOT NULL, reason TEXT NOT NULL, snapshot TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(document_id,revision)
                );
                CREATE INDEX IF NOT EXISTS records_power ON records(power_w);
            """)
            connection.execute("BEGIN IMMEDIATE")
            columns = {r["name"] for r in connection.execute("PRAGMA table_info(documents)")}
            if "rules_version" not in columns:
                connection.execute(
                    "ALTER TABLE documents ADD COLUMN rules_version TEXT NOT NULL DEFAULT 'legacy-v1'"
                )
            columns = {r["name"] for r in connection.execute("PRAGMA table_info(records)")}
            for column in ("voltage_kind", "current_kind"):
                if column not in columns:
                    connection.execute(f"ALTER TABLE records ADD COLUMN {column} TEXT")
            self._upgrade_rules(connection)

    def describe_backend(self):
        result = self.parser.describe()
        if self.mapper:
            result = {**result, "field_mapper": self.mapper.describe()}
        return result

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.settings.database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.create_function("spec_decimal_compare", 2, decimal_compare, deterministic=True)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _verify_source(self, row):
        source = (self.settings.data / row["pdf_path"]).resolve()
        try:
            valid = (
                source.is_relative_to(self.settings.data.resolve())
                and source.is_file()
                and digest(source) == row["sha256"]
            )
        except OSError:
            valid = False
        if not valid:
            raise ProjectError("原始 PDF 缺失或指纹变化，无法保证证据一致。")
        return source

    @staticmethod
    def _audit(connection, document_id, revision, action, details):
        connection.execute(
            "INSERT INTO audit(document_id,revision,action,details) VALUES(?,?,?,?)",
            (document_id, revision, action, json.dumps(details, ensure_ascii=False)),
        )

    def _snapshot(self, connection, document_id, action, reason=""):
        row = connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        connection.execute(
            "INSERT OR IGNORE INTO document_versions(document_id,revision,action,reason,snapshot) "
            "VALUES(?,?,?,?,?)",
            (document_id, row["revision"], action, reason, json.dumps(dict(row), ensure_ascii=False)),
        )

    @staticmethod
    def _decode(row):
        document = dict(row)
        for key in JSON_COLUMNS:
            document[key] = json.loads(document[key])
        return document

    def _read_document(self, connection, document_id):
        row = connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if row is None:
            raise ProjectError("文档不存在。")
        self._verify_source(row)
        document = self._decode(row)
        document["audit"] = [
            dict(event)
            for event in connection.execute(
                "SELECT revision,action,details,created_at FROM audit WHERE document_id=? ORDER BY id",
                (document_id,),
            )
        ]
        for event in document["audit"]:
            event["details"] = json.loads(event["details"])
        document["versions"] = []
        for version in connection.execute(
            "SELECT revision,action,reason,snapshot,created_at FROM document_versions "
            "WHERE document_id=? ORDER BY revision DESC",
            (document_id,),
        ):
            raw = json.loads(version["snapshot"])
            document["versions"].append(
                {
                    "revision": version["revision"],
                    "action": version["action"],
                    "reason": version["reason"],
                    "status": raw["status"],
                    "backend": json.loads(raw["backend"])["kind"],
                    "created_at": version["created_at"],
                }
            )
        return document

    @staticmethod
    def _check_revision(row, revision):
        if row is None or row["revision"] != revision:
            raise ProjectError("文档不存在或版本已变化，请刷新后再试。")

    @staticmethod
    def _write_record(connection, document_id, fields):
        keys = list(SCHEMA)
        columns = ["document_id"] + keys + ["voltage_kind", "current_kind"]
        values = [document_id] + [fields[k]["value"] for k in keys]
        values += [fields["voltage_v"].get("signal"), fields["current_a"].get("signal")]
        assignments = ",".join(f"{column}=excluded.{column}" for column in columns[1:])
        connection.execute(
            f"INSERT INTO records ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
            f"ON CONFLICT(document_id) DO UPDATE SET {assignments}",
            values,
        )

    def _upgrade_rules(self, connection):
        rows = connection.execute(
            "SELECT * FROM documents WHERE rules_version != ?", (UNIT_RULES_VERSION,)
        ).fetchall()
        for row in rows:
            self._snapshot(connection, row["id"], "legacy_import", "升级前已有状态的快照")
            observations = [
                normalized_observation(Observation.model_validate(o)) for o in json.loads(row["observations"])
            ]
            fields, issues = validate(observations)
            old_fields = json.loads(row["fields"])
            changed_meaning = set(old_fields) != set(fields) or any(
                old_fields[k].get("value") != v["value"] or old_fields[k].get("signal") != v.get("signal")
                for k, v in fields.items()
                if k in old_fields
            )
            try:
                self._verify_source(row)
            except ProjectError:
                issues.append({"field": "_source", "code": "source_changed", "message": "原文缺失或指纹变化"})
            keep_approval = row["status"] == "approved" and not issues and not changed_meaning
            status = "approved" if keep_approval else "needs_review" if issues else "ready"
            revision = row["revision"] + 1
            connection.execute(
                "UPDATE documents SET observations=?,fields=?,issues=?,status=?,revision=?,rules_version=? "
                "WHERE id=?",
                (
                    json.dumps([o.model_dump() for o in observations]),
                    json.dumps(fields),
                    json.dumps(issues),
                    status,
                    revision,
                    UNIT_RULES_VERSION,
                    row["id"],
                ),
            )
            if keep_approval:
                self._write_record(connection, row["id"], fields)
            else:
                connection.execute("DELETE FROM records WHERE document_id=?", (row["id"],))
            reason = "升级单位规则；语义或数值变化的记录需重新确认，旧状态已保留"
            self._audit(
                connection,
                row["id"],
                revision,
                "rules_revalidated",
                {"reason": reason, "rules_version": UNIT_RULES_VERSION, "approval_retained": keep_approval},
            )
            self._snapshot(connection, row["id"], "rules_revalidated", reason)

    def list_documents(self):
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT id,filename,source,status,revision,processing_ms,created_at,rules_version,"
                    "json_array_length(issues) AS issue_count,json_array_length(pages) AS page_count,"
                    "(SELECT json_extract(details,'$.cache_hit') FROM audit a "
                    "WHERE a.document_id=documents.id "
                    "AND a.action IN ('extracted','reprocessed') ORDER BY a.id DESC LIMIT 1) AS cache_hit "
                    "FROM documents ORDER BY created_at,id"
                )
            ]

    def get(self, document_id):
        with self.connect() as connection:
            return self._read_document(connection, document_id)

    def get_version(self, document_id, revision):
        with self.connect() as connection:
            current = self._read_document(connection, document_id)
            version = connection.execute(
                "SELECT snapshot FROM document_versions WHERE document_id=? AND revision=?",
                (document_id, revision),
            ).fetchone()
            if version is None:
                raise ProjectError("该历史版本没有可用快照。")
            result = self._decode(json.loads(version["snapshot"]))
            result.update(
                historical=True,
                latest_revision=current["revision"],
                audit=[event for event in current["audit"] if event["revision"] <= revision],
            )
            return result

    def ingest(self, path, *, filename=None, source="Local upload; provenance supplied by user"):
        started = time.perf_counter()
        if not source.strip() or len(source) > 1000:
            raise ProjectError("来源说明需为 1–1000 字符。")
        preflight(path)
        sha = digest(path)
        document_id = "d-" + sha
        with self.connect() as connection:
            existing = connection.execute("SELECT id FROM documents WHERE sha256=?", (sha,)).fetchone()
        if existing:
            return {"document": self.get(existing["id"]), "duplicate": True}
        pages, cached = self.parser.parse(path)
        observations = self.mapper.extract(pages) if self.mapper else extract_fields(pages)
        fields, issues = validate(observations)
        folder = self.settings.data / "documents"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{sha}.pdf"
        if not target.exists():
            with tempfile.NamedTemporaryFile(dir=folder, suffix=".pdf", delete=False) as file:
                temporary = Path(file.name)
            try:
                shutil.copyfile(path, temporary)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO documents (id,sha256,filename,source,pdf_path,pages,observations,"
                "fields,issues,backend,status,processing_ms,rules_version) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    document_id,
                    sha,
                    Path(filename or path.name).name[:200],
                    source.strip(),
                    str(target.relative_to(self.settings.data)),
                    json.dumps([p.model_dump() for p in pages]),
                    json.dumps([o.model_dump() for o in observations]),
                    json.dumps(fields),
                    json.dumps(issues),
                    json.dumps(self.describe_backend()),
                    "needs_review" if issues else "ready",
                    elapsed,
                    UNIT_RULES_VERSION,
                ),
            )
            inserted = bool(connection.execute("SELECT changes()").fetchone()[0])
            if inserted:
                self._audit(connection, document_id, 1, "extracted", {"cache_hit": cached, "sha256": sha})
                self._snapshot(connection, document_id, "extracted")
            response = self._read_document(connection, document_id)
        return {"document": response, "duplicate": not inserted}

    def correct(self, document_id, field, correction):
        if field not in SCHEMA:
            raise ProjectError("未知字段。")
        quantity = parse_quantity(field, correction.raw_value)
        if quantity.error:
            raise ProjectError(f"修订值无效：{ERROR_MESSAGES[quantity.error]}。")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            self._check_revision(row, correction.revision)
            self._verify_source(row)
            if row["status"] == "approved":
                raise ProjectError("已入库文档不可直接修订；请先重新解析，生成待审版本。")
            if correction.page > len(json.loads(row["pages"])):
                raise ProjectError("修订来源页码超出文档范围。")
            observations = [Observation.model_validate(o) for o in json.loads(row["observations"])]
            observations.append(
                normalized_observation(
                    Observation(
                        name=field,
                        raw_value=correction.raw_value,
                        evidence=EvidenceLine(
                            text="人工修订；以审阅说明为准", page=correction.page, bbox=[0, 0, 1, 1]
                        ),
                        origin="manual",
                        reason=correction.reason,
                    )
                )
            )
            fields, issues = validate(observations)
            revision = row["revision"] + 1
            connection.execute(
                "UPDATE documents SET observations=?,fields=?,issues=?,status=?,revision=?,rules_version=? "
                "WHERE id=?",
                (
                    json.dumps([o.model_dump() for o in observations]),
                    json.dumps(fields),
                    json.dumps(issues),
                    "needs_review" if issues else "ready",
                    revision,
                    UNIT_RULES_VERSION,
                    document_id,
                ),
            )
            self._audit(
                connection,
                document_id,
                revision,
                "corrected",
                {
                    "field": field,
                    "value": quantity.value,
                    "raw_value": correction.raw_value,
                    "signal": quantity.signal,
                    "exact_value": quantity.exact_value,
                    "page": correction.page,
                    "reason": correction.reason,
                },
            )
            self._snapshot(connection, document_id, "corrected", correction.reason)
            response = self._read_document(connection, document_id)
        return response

    def approve(self, document_id, revision):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            self._check_revision(row, revision)
            self._verify_source(row)
            if row["status"] == "approved":
                return self._read_document(connection, document_id)
            fields, issues = validate(
                [Observation.model_validate(o) for o in json.loads(row["observations"])]
            )
            if issues or set(fields) != set(SCHEMA):
                raise ProjectError("仍有缺失或冲突字段，必须先修订再入库。")
            self._write_record(connection, document_id, fields)
            connection.execute(
                "UPDATE documents SET status='approved',fields=?,revision=revision+1 WHERE id=?",
                (json.dumps(fields), document_id),
            )
            self._audit(connection, document_id, revision + 1, "approved", {"fields": len(SCHEMA)})
            self._snapshot(connection, document_id, "approved")
            response = self._read_document(connection, document_id)
        return response

    def reparse(self, document_id, revision, reason):
        if not 3 <= len(reason.strip()) <= 500:
            raise ProjectError("重新解析需填写 3–500 字符的理由。")
        current = self.get(document_id)
        self._check_revision(current, revision)
        started = time.perf_counter()
        pages, _ = self.parser.parse(self.settings.data / current["pdf_path"], cached=False)
        observations = self.mapper.extract(pages) if self.mapper else extract_fields(pages)
        fields, issues = validate(observations)
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            self._check_revision(row, revision)
            self._verify_source(row)
            self._snapshot(connection, document_id, "legacy_import", "重新解析前的状态")
            connection.execute("DELETE FROM records WHERE document_id=?", (document_id,))
            connection.execute(
                "UPDATE documents SET pages=?,observations=?,fields=?,issues=?,backend=?,status=?,"
                "revision=revision+1,processing_ms=?,rules_version=? WHERE id=?",
                (
                    json.dumps([p.model_dump() for p in pages]),
                    json.dumps([o.model_dump() for o in observations]),
                    json.dumps(fields),
                    json.dumps(issues),
                    json.dumps(self.describe_backend()),
                    "needs_review" if issues else "ready",
                    elapsed,
                    UNIT_RULES_VERSION,
                    document_id,
                ),
            )
            self._audit(
                connection,
                document_id,
                revision + 1,
                "reprocessed",
                {
                    "reason": reason.strip(),
                    "cache_hit": False,
                    "previous_revision": revision,
                    "backend_before": json.loads(row["backend"]),
                    "backend_after": self.describe_backend(),
                    "manual_corrections_carried_forward": False,
                },
            )
            self._snapshot(connection, document_id, "reprocessed", reason.strip())
            response = self._read_document(connection, document_id)
        return response

    def query(self, request):
        clauses, parameters = [], []
        for condition in request.filters:
            quantity = parse_quantity(condition.field, condition.raw_value)
            if quantity.error:
                raise ProjectError(f"筛选值无效：{ERROR_MESSAGES[quantity.error]}。")
            if condition.field in ("model", "ip_rating"):
                if condition.op != "=":
                    raise ProjectError("型号和防护等级只支持精确匹配。")
                clauses.append(f"r.{condition.field} = ?")
                parameters.append(quantity.value)
            else:
                clauses.append(
                    f"spec_decimal_compare(json_extract(d.fields,'$.{condition.field}.exact_value'),?) "
                    f"{condition.op} 0"
                )
                parameters.append(quantity.exact_value)
                if quantity.signal:
                    column = "voltage_kind" if condition.field == "voltage_v" else "current_kind"
                    clauses.append(f"r.{column} = ?")
                    parameters.append(quantity.signal)
        sql = (
            "SELECT r.*,d.filename,d.source,d.fields,d.revision "
            "FROM records r JOIN documents d ON d.id=r.document_id"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY r.model,r.document_id LIMIT ?"
        parameters.append(request.limit)
        with self.connect() as connection:
            results = [dict(row) for row in connection.execute(sql, parameters)]
            for row in results:
                self._read_document(connection, row["document_id"])
                row["evidence"] = json.loads(row.pop("fields"))
        return {
            "results": results,
            "count": len(results),
            "sql": sql,
            "parameters": parameters,
            "note": "仅查询当前已审核版本；数值按十进制比较，未写 AC/DC 时不限定该属性。",
        }
