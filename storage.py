import sqlite3
from pathlib import Path
from typing import List, Dict, Optional


class LocalStorage:
    def __init__(self, db_path: str = "tongue_records.db"):
        self.db_path = str(Path(db_path))
        self._init_db()

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS analysis_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    image_path TEXT NOT NULL,
                    brief_result TEXT,
                    full_result TEXT,
                    model_name TEXT,
                    confidence REAL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def add_record(
        self,
        created_at: str,
        image_path: str,
        brief_result: str,
        full_result: str,
        model_name: str = "",
        confidence: Optional[float] = None,
    ):
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO analysis_records
                (created_at, image_path, brief_result, full_result, model_name, confidence)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (created_at, image_path, brief_result, full_result, model_name, confidence),
            )
            conn.commit()
        finally:
            conn.close()

    def list_records(self, limit: int = 50) -> List[Dict]:
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, created_at, image_path, brief_result, full_result, model_name, confidence
                FROM analysis_records
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cur.fetchall()
            return [
                {
                    "id": r[0],
                    "created_at": r[1],
                    "image_path": r[2],
                    "brief_result": r[3] or "",
                    "full_result": r[4] or "",
                    "model_name": r[5] or "",
                    "confidence": r[6],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_record(self, record_id: int) -> Optional[Dict]:
        """按 id 获取单条记录。"""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, created_at, image_path, brief_result, full_result, model_name, confidence
                FROM analysis_records
                WHERE id = ?
                """,
                (record_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "created_at": row[1],
                "image_path": row[2],
                "brief_result": row[3] or "",
                "full_result": row[4] or "",
                "model_name": row[5] or "",
                "confidence": row[6],
            }
        finally:
            conn.close()

    def delete_record(self, record_id: int) -> bool:
        """删除单条记录。"""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM analysis_records WHERE id = ?", (record_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def delete_all_records(self) -> None:
        """清空所有历史记录。"""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM analysis_records")
            conn.commit()
        finally:
            conn.close()

