import json
import os
import db_adapter as sqlite3

import pandas as pd

from utils import log_error, log_info


class ICDService:
    def __init__(self, excel_path: str, data_dir: str):
        self.excel_path = excel_path
        self.db_path = os.path.join(data_dir, "icd10_smart.db")

        # Initialize the database immediately upon class instantiation
        self._initialize_database()

    def _initialize_database(self):
        """
        Checks if the SQLite database exists. If not, creates it by reading the raw Excel file.
        Parses ICD-10-CM and ICD-10-PCS sheets and builds indices for hierarchical queries.
        """
        if getattr(sqlite3, "USE_POSTGRES", False):
            # In PostgreSQL mode, db_path is not meaningful. Check existing tables instead.
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM diagnoses LIMIT 1")
                conn.close()
                log_info("ICD tables already exist in PostgreSQL; skip initialization.")
                return
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                log_info("ICD tables not found in PostgreSQL. Initializing from Excel...")
        elif os.path.exists(self.db_path):
            if os.path.getsize(self.db_path) == 0:
                log_info("ICD Database is empty. Removing and re-initializing...")
                os.remove(self.db_path)
            else:
                log_info(f"ICD Database found at: {self.db_path}")
                return

        log_info(f"Initializing database from Excel: {self.excel_path}")

        if not os.path.exists(self.excel_path):
            log_error(f"Excel file not found at: {self.excel_path}")
            raise FileNotFoundError(f"Excel file not found at: {self.excel_path}")

        conn = sqlite3.connect(self.db_path)
        try:
            xls = pd.ExcelFile(self.excel_path)

            # 1. Process ICD-10-CM (Diagnoses)
            # Logic: Find sheet with 'CM' in name, excluding 'deleted' sheets
            sheet_cm = next(
                (s for s in xls.sheet_names if "CM" in s and "刪除" not in s), None
            )
            if sheet_cm:
                log_info(f"Processing Diagnoses sheet: {sheet_cm}")
                df = pd.read_excel(xls, sheet_name=sheet_cm)

                # Select specific columns: Code, English Name, Chinese Name
                # Assumes columns [0, 2, 3] based on your specific Excel structure
                df = df.iloc[:, [0, 2, 3]]
                df.columns = ["code", "name_en", "name_zh"]
                df = df.dropna(subset=["code"])

                # Feature Engineering: Create 'category' (first 3 chars) for hierarchical logic
                df["category"] = df["code"].astype(str).str[:3]
                self._replace_table_from_df(
                    conn, "diagnoses", ["code", "name_en", "name_zh", "category"], df
                )

            # 2. Process ICD-10-PCS (Procedures)
            sheet_pcs = next(
                (s for s in xls.sheet_names if "PCS" in s and "刪除" not in s), None
            )
            if sheet_pcs:
                log_info(f"Processing Procedures sheet: {sheet_pcs}")
                df = pd.read_excel(xls, sheet_name=sheet_pcs)
                df = df.iloc[:, [0, 2, 3]]
                df.columns = ["code", "name_en", "name_zh"]
                df = df.dropna(subset=["code"])
                self._replace_table_from_df(
                    conn, "procedures", ["code", "name_en", "name_zh"], df
                )

            # Create indices for performance
            indices = [
                "CREATE INDEX IF NOT EXISTS idx_diag_code ON diagnoses(code)",
                "CREATE INDEX IF NOT EXISTS idx_diag_cat ON diagnoses(category)",
                "CREATE INDEX IF NOT EXISTS idx_diag_name_zh ON diagnoses(name_zh)",
                "CREATE INDEX IF NOT EXISTS idx_proc_code ON procedures(code)",
                "CREATE INDEX IF NOT EXISTS idx_proc_name_zh ON procedures(name_zh)",
            ]
            for sql in indices:
                conn.execute(sql)

            log_info("Database initialization complete.")

        except Exception as e:
            log_error(f"Database initialization failed: {e}")
            conn.close()
            if not getattr(sqlite3, "USE_POSTGRES", False) and os.path.exists(
                self.db_path
            ):
                os.remove(self.db_path)
                log_info(f"Removed incomplete database: {self.db_path}")
            raise
        finally:
            conn.close()

    def _replace_table_from_df(self, conn, table_name: str, columns: list[str], df):
        """
        Replace table contents from a DataFrame in a DB-agnostic way.
        Works for both sqlite and postgres adapter.
        """
        cursor = conn.cursor()
        cursor.execute(f"DROP TABLE IF EXISTS {table_name}")
        col_defs = ", ".join([f"{col} TEXT" for col in columns])
        cursor.execute(f"CREATE TABLE {table_name} ({col_defs})")

        placeholders = ", ".join(["?" for _ in columns])
        insert_sql = (
            f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})"
        )
        rows = []
        for row in df[columns].itertuples(index=False, name=None):
            rows.append(tuple("" if v is None else str(v) for v in row))
        if rows:
            cursor.executemany(insert_sql, rows)
        conn.commit()

    def _db_ready(self) -> bool:
        if getattr(sqlite3, "USE_POSTGRES", False):
            try:
                conn = sqlite3.connect(self.db_path)
                exists = sqlite3.table_exists(conn, "diagnoses")
                conn.close()
                return exists
            except Exception:
                return False
        return os.path.exists(self.db_path)

    def _query_db(self, sql: str, params: tuple = ()) -> list:
        """Helper function to execute SQL queries and return results as a list of dicts."""
        if not self._db_ready():
            log_error(f"Database not found at: {self.db_path}")
            return []

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = [dict(row) for row in cursor.fetchall()]
            conn.close()
            log_info(f"Query executed successfully, returned {len(rows)} rows")
            return rows
        except Exception as e:
            log_error(f"Database query failed: {e}")
            log_error(f"SQL: {sql}, Params: {params}")
            return []

    # --- Core Functionalities (Ported from your original code) ---

    def search_codes(self, keyword: str, type: str = "all") -> str:
        """
        Search for ICD-10 diagnosis or procedure codes.
        """
        term = f"%{keyword}%"
        results = {}

        if type in ["diagnosis", "all"]:
            sql = "SELECT code, name_zh, name_en FROM diagnoses WHERE code LIKE ? OR name_zh LIKE ? OR name_en LIKE ? LIMIT 10"
            results["diagnoses"] = self._query_db(sql, (term, term, term))

        if type in ["procedure", "all"]:
            sql = "SELECT code, name_zh, name_en FROM procedures WHERE code LIKE ? OR name_zh LIKE ? OR name_en LIKE ? LIMIT 10"
            results["procedures"] = self._query_db(sql, (term, term, term))

        if not results.get("diagnoses") and not results.get("procedures"):
            return f"No results found for '{keyword}'."

        return json.dumps(results, ensure_ascii=False)

    def infer_complications(self, code: str) -> str:
        """
        Infers potential complications by finding child codes.
        """
        # Strategy 1: Find codes starting with the input code (Parent -> Children relationship)
        sql_children = "SELECT code, name_zh FROM diagnoses WHERE code LIKE ? AND code != ? ORDER BY code LIMIT 15"
        children = self._query_db(sql_children, (f"{code}%", code))

        # Strategy 2: If no children found, looks for siblings in the same category
        if not children:
            category = code.split(".")[0] if "." in code else code[:3]
            sql_siblings = "SELECT code, name_zh FROM diagnoses WHERE category = ? AND code != ? LIMIT 10"
            children = self._query_db(sql_siblings, (category, code))
            return json.dumps(
                {
                    "message": f"Code {code} is specific. Showing related codes in category {category}:",
                    "related_codes": children,
                },
                ensure_ascii=False,
            )

        return json.dumps(
            {"base_code": code, "potential_complications_or_specifics": children},
            ensure_ascii=False,
        )

    def get_nearby_codes(self, code: str) -> str:
        """
        Retrieves codes immediately preceding and following the target code.
        """
        sql = """
        SELECT * FROM (
            SELECT code, name_zh, 'prev' as rel FROM diagnoses WHERE code < ? ORDER BY code DESC LIMIT 2
        ) UNION
        SELECT * FROM (
            SELECT code, name_zh, 'next' as rel FROM diagnoses WHERE code > ? ORDER BY code ASC LIMIT 2
        )
        ORDER BY code
        """
        neighbors = self._query_db(sql, (code, code))
        return json.dumps(
            {"target": code, "nearby_options": neighbors}, ensure_ascii=False
        )

    def get_conflict_info(self, diagnosis_code: str, procedure_code: str) -> str:
        """
        Retrieves definitions for a diagnosis and a procedure to help analyze conflicts.
        """
        try:
            diag = self._query_db(
                "SELECT * FROM diagnoses WHERE code = ?", (diagnosis_code,)
            )
            proc = self._query_db(
                "SELECT * FROM procedures WHERE code = ?", (procedure_code,)
            )

            result = {
                "diagnosis_info": diag[0] if diag else "Diagnosis not found",
                "procedure_info": proc[0] if proc else "Procedure not found",
                "instruction": "Analyze the above for potential contraindications or medical conflicts.",
            }
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            log_error(f"Error in get_conflict_info: {e}")
            return json.dumps(
                {
                    "error": f"Failed to retrieve conflict information: {str(e)}",
                    "diagnosis_code": diagnosis_code,
                    "procedure_code": procedure_code,
                },
                ensure_ascii=False,
            )
