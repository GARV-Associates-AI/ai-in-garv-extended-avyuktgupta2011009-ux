# database.py
# ============================================================
# Database layer — all SQLite operations
# UPDATED: Day 16 — Master Securities Database (Phase 1-4)
# UPDATED: Day 16 — Fast cache for reconciliation (Phase 4 fix)
# ============================================================

import sqlite3
import json
import re
from datetime import datetime

DB_NAME = "capital_gains.db"


# ------------------------------------------------------------
# INITIALISE DATABASE
# ------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            pan           TEXT    UNIQUE NOT NULL,
            email         TEXT,
            phone         TEXT,
            password_hash TEXT    DEFAULT NULL,
            created_at    TEXT    DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id     INTEGER NOT NULL,
            fin_year      TEXT    NOT NULL,
            date          TEXT    NOT NULL,
            type          TEXT    NOT NULL,
            company       TEXT    NOT NULL,
            isin          TEXT    DEFAULT '',
            quantity      INTEGER NOT NULL,
            amount        REAL    NOT NULL,
            buy_expenses  REAL    DEFAULT 0,
            sell_expenses REAL    DEFAULT 0,
            stt           REAL    DEFAULT 0,
            notes         TEXT    DEFAULT '',
            added_on      TEXT    DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS fmv_data (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            company   TEXT    NOT NULL,
            fmv       REAL    NOT NULL,
            UNIQUE(client_id, company),
            FOREIGN KEY (client_id) REFERENCES clients(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS losses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id   INTEGER NOT NULL,
            loss_year   TEXT    NOT NULL,
            stcl        REAL    DEFAULT 0,
            ltcl        REAL    DEFAULT 0,
            stcl_used   REAL    DEFAULT 0,
            ltcl_used   REAL    DEFAULT 0,
            source      TEXT    DEFAULT 'auto',
            notes       TEXT    DEFAULT '',
            created_at  TEXT    DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(client_id, loss_year, source),
            FOREIGN KEY (client_id) REFERENCES clients(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS securities (
            isin           TEXT PRIMARY KEY,
            official_name  TEXT NOT NULL,
            aliases        TEXT DEFAULT '[]',
            exchange       TEXT,
            created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at     TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    try:
        c.execute("SELECT stt FROM transactions LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE transactions ADD COLUMN stt REAL DEFAULT 0")

    try:
        c.execute("SELECT password_hash FROM clients LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE clients ADD COLUMN password_hash TEXT DEFAULT NULL")

    conn.commit()
    conn.close()


# ============================================================
# CLIENT FUNCTIONS
# ============================================================

def add_client(name, pan, email="", phone="", password=None):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()

    password_hash = None
    if password and password.strip():
        from werkzeug.security import generate_password_hash
        password_hash = generate_password_hash(password.strip())

    try:
        c.execute("""
            INSERT INTO clients (name, pan, email, phone, password_hash)
            VALUES (?, ?, ?, ?, ?)
        """, (
            name.strip(), pan.upper().strip(),
            email.strip(), phone.strip(), password_hash
        ))
        conn.commit()
        return c.lastrowid, None
    except sqlite3.IntegrityError:
        return None, f"A client with PAN '{pan.upper()}' already exists."
    finally:
        conn.close()


def get_all_clients():
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("""
        SELECT cl.id, cl.name, cl.pan, cl.email, cl.phone,
               COUNT(tx.id) AS tx_count, cl.created_at,
               cl.password_hash IS NOT NULL AS has_password
        FROM clients cl
        LEFT JOIN transactions tx ON tx.client_id = cl.id
        GROUP BY cl.id ORDER BY cl.name ASC
    """)
    rows = c.fetchall()
    conn.close()
    return rows


def get_client(client_id):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    row = c.fetchone()
    conn.close()
    return row


def delete_client(client_id):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("DELETE FROM transactions WHERE client_id = ?", (client_id,))
    c.execute("DELETE FROM fmv_data    WHERE client_id = ?", (client_id,))
    c.execute("DELETE FROM losses      WHERE client_id = ?", (client_id,))
    c.execute("DELETE FROM clients     WHERE id = ?",        (client_id,))
    conn.commit()
    conn.close()


def update_client_details(client_id, name, pan, email="", phone=""):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    try:
        c.execute("""
            UPDATE clients SET name = ?, pan = ?, email = ?, phone = ?
            WHERE id = ?
        """, (
            name.strip(), pan.upper().strip(),
            email.strip() if email else "",
            phone.strip() if phone else "",
            client_id
        ))
        conn.commit()
        return True, None
    except sqlite3.IntegrityError:
        return False, f"PAN '{pan.upper()}' is already used by another client."
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


# ============================================================
# PASSWORD FUNCTIONS
# ============================================================

def verify_client_password(client_id, password):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("SELECT password_hash FROM clients WHERE id = ?", (client_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return False
    password_hash = row[0]
    if password_hash is None:
        return True
    from werkzeug.security import check_password_hash
    return check_password_hash(password_hash, password)


def set_client_password(client_id, new_password):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    if new_password and new_password.strip():
        from werkzeug.security import generate_password_hash
        password_hash = generate_password_hash(new_password.strip())
    else:
        password_hash = None
    c.execute("UPDATE clients SET password_hash = ? WHERE id = ?",
              (password_hash, client_id))
    conn.commit()
    conn.close()


def client_has_password(client_id):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("SELECT password_hash FROM clients WHERE id = ?", (client_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return False
    return row[0] is not None


# ============================================================
# TRANSACTION FUNCTIONS
# ============================================================

def add_transaction(client_id, fin_year, date, type_, company, isin,
                    quantity, amount, buy_expenses, sell_expenses,
                    stt=0, notes=""):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("""
        INSERT INTO transactions
        (client_id, fin_year, date, type, company, isin,
         quantity, amount, buy_expenses, sell_expenses, stt, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        client_id, fin_year, date,
        type_.upper().strip(), company.upper().strip(), isin or "",
        int(quantity), float(amount),
        float(buy_expenses or 0), float(sell_expenses or 0),
        float(stt or 0), notes or ""
    ))
    conn.commit()
    tx_id = c.lastrowid
    conn.close()
    return tx_id


def add_transactions_bulk(client_id, fin_year, rows):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    for row in rows:
        c.execute("""
            INSERT INTO transactions
            (client_id, fin_year, date, type, company, isin,
             quantity, amount, buy_expenses, sell_expenses, stt, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            client_id, fin_year, row["date"],
            str(row["type"]).upper().strip(),
            str(row["company"]).upper().strip(),
            row.get("isin", "") or "",
            int(row["quantity"]), float(row["amount"]),
            float(row.get("buy_expenses",  0) or 0),
            float(row.get("sell_expenses", 0) or 0),
            float(row.get("stt", 0) or 0),
            row.get("notes", "") or ""
        ))
    conn.commit()
    conn.close()


def get_transactions(client_id, fin_year):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("""
        SELECT date, type, company, isin, quantity, amount,
               buy_expenses, sell_expenses, stt, notes
        FROM transactions
        WHERE client_id = ? AND fin_year = ?
        ORDER BY company ASC, date ASC
    """, (client_id, fin_year))
    rows = c.fetchall()
    conn.close()
    return [
        {"date": r[0], "type": r[1], "company": r[2], "isin": r[3],
         "quantity": r[4], "amount": r[5],
         "buy_expenses": r[6], "sell_expenses": r[7],
         "stt": r[8], "notes": r[9]} for r in rows
    ]


def get_transactions_display(client_id, fin_year):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("""
        SELECT id, date, type, company, isin, quantity, amount,
               buy_expenses, sell_expenses, stt, notes
        FROM transactions
        WHERE client_id = ? AND fin_year = ?
        ORDER BY date ASC, company ASC
    """, (client_id, fin_year))
    rows = c.fetchall()
    conn.close()
    return rows


def get_transaction_by_id(tx_id):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("""
        SELECT id, client_id, fin_year, date, type, company, isin,
               quantity, amount, buy_expenses, sell_expenses, stt, notes
        FROM transactions WHERE id = ?
    """, (tx_id,))
    row = c.fetchone()
    conn.close()
    return row


def update_transaction(tx_id, date, type_, company, isin,
                       quantity, amount, buy_expenses,
                       sell_expenses, stt=0, notes=""):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("""
        UPDATE transactions
        SET date = ?, type = ?, company = ?, isin = ?,
            quantity = ?, amount = ?, buy_expenses = ?,
            sell_expenses = ?, stt = ?, notes = ?
        WHERE id = ?
    """, (
        date, type_.upper().strip(), company.upper().strip(),
        isin or "", int(quantity), float(amount),
        float(buy_expenses or 0), float(sell_expenses or 0),
        float(stt or 0), notes or "", tx_id
    ))
    conn.commit()
    conn.close()


def delete_transaction(tx_id):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
    conn.commit()
    conn.close()


def get_financial_years(client_id):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("""
        SELECT DISTINCT fin_year FROM transactions
        WHERE client_id = ? ORDER BY fin_year DESC
    """, (client_id,))
    rows = [r[0] for r in c.fetchall()]
    conn.close()
    return rows


def get_transaction_count(client_id, fin_year):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("""
        SELECT COUNT(*) FROM transactions
        WHERE client_id = ? AND fin_year = ?
    """, (client_id, fin_year))
    count = c.fetchone()[0]
    conn.close()
    return count


# ============================================================
# FMV FUNCTIONS
# ============================================================

def save_fmv(client_id, company, fmv):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("""
        INSERT INTO fmv_data (client_id, company, fmv)
        VALUES (?, ?, ?)
        ON CONFLICT(client_id, company) DO UPDATE SET fmv = excluded.fmv
    """, (client_id, company.upper().strip(), float(fmv)))
    conn.commit()
    conn.close()


def get_fmv(client_id):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("SELECT company, fmv FROM fmv_data WHERE client_id = ?", (client_id,))
    rows = c.fetchall()
    conn.close()
    return {row[0]: row[1] for row in rows}


def get_all_fmv(client_id):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("""
        SELECT company, fmv FROM fmv_data WHERE client_id = ?
        ORDER BY company ASC
    """, (client_id,))
    rows = c.fetchall()
    conn.close()
    return rows


def delete_fmv(client_id, company):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("DELETE FROM fmv_data WHERE client_id = ? AND company = ?",
              (client_id, company.upper().strip()))
    conn.commit()
    conn.close()


def save_fmv_bulk(client_id, entries):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    for entry in entries:
        c.execute("""
            INSERT INTO fmv_data (client_id, company, fmv)
            VALUES (?, ?, ?)
            ON CONFLICT(client_id, company) DO UPDATE SET fmv = excluded.fmv
        """, (client_id, entry["company"].upper().strip(), float(entry["fmv"])))
    conn.commit()
    conn.close()


# ============================================================
# LOSS FUNCTIONS
# ============================================================

def add_loss(client_id, loss_year, stcl=0, ltcl=0,
             source="auto", notes=""):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    try:
        c.execute("""
            INSERT INTO losses
            (client_id, loss_year, stcl, ltcl, source, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_id, loss_year, source) DO UPDATE SET
                stcl = excluded.stcl, ltcl = excluded.ltcl,
                notes = excluded.notes
        """, (client_id, loss_year.strip(),
              float(stcl or 0), float(ltcl or 0), source, notes or ""))
        conn.commit()
        return c.lastrowid, None
    except Exception as e:
        return None, str(e)
    finally:
        conn.close()


def get_all_losses(client_id):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("""
        SELECT id, loss_year, stcl, ltcl, stcl_used, ltcl_used,
               source, notes, created_at
        FROM losses WHERE client_id = ?
        ORDER BY loss_year ASC, created_at ASC
    """, (client_id,))
    rows = c.fetchall()
    conn.close()
    return rows


def get_loss_by_id(loss_id):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("""
        SELECT id, client_id, loss_year, stcl, ltcl,
               stcl_used, ltcl_used, source, notes, created_at
        FROM losses WHERE id = ?
    """, (loss_id,))
    row = c.fetchone()
    conn.close()
    return row


def update_loss(loss_id, loss_year, stcl, ltcl, notes=""):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("""
        UPDATE losses SET loss_year = ?, stcl = ?, ltcl = ?, notes = ?
        WHERE id = ?
    """, (loss_year.strip(), float(stcl or 0), float(ltcl or 0),
          notes or "", loss_id))
    conn.commit()
    conn.close()


def delete_loss(loss_id):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("DELETE FROM losses WHERE id = ?", (loss_id,))
    conn.commit()
    conn.close()


def get_available_losses(client_id, current_year):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("""
        SELECT id, loss_year, stcl, ltcl, stcl_used, ltcl_used, source
        FROM losses WHERE client_id = ? AND loss_year < ?
        ORDER BY loss_year ASC
    """, (client_id, current_year))
    rows = c.fetchall()
    conn.close()

    available = []
    try:
        current_end_year = int(current_year.split("-")[0])
    except Exception:
        current_end_year = 2024

    for row in rows:
        loss_id, loss_year, stcl, ltcl, stcl_used, ltcl_used, source = row
        try:
            loss_start_year = int(loss_year.split("-")[0])
            years_passed = current_end_year - loss_start_year
            if years_passed > 8:
                continue
            years_remaining = 8 - years_passed
        except Exception:
            years_remaining = 0

        stcl_remaining = max(0, (stcl or 0) - (stcl_used or 0))
        ltcl_remaining = max(0, (ltcl or 0) - (ltcl_used or 0))
        if stcl_remaining <= 0 and ltcl_remaining <= 0:
            continue

        available.append({
            "id": loss_id, "loss_year": loss_year,
            "stcl": stcl or 0, "ltcl": ltcl or 0,
            "stcl_remaining": stcl_remaining, "ltcl_remaining": ltcl_remaining,
            "stcl_used": stcl_used or 0, "ltcl_used": ltcl_used or 0,
            "source": source, "years_remaining": years_remaining,
        })
    return available


def update_loss_used(loss_id, stcl_used, ltcl_used):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("""
        UPDATE losses SET stcl_used = ?, ltcl_used = ?
        WHERE id = ?
    """, (float(stcl_used or 0), float(ltcl_used or 0), loss_id))
    conn.commit()
    conn.close()


def reset_loss_usage(client_id, from_year):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("""
        UPDATE losses SET stcl_used = 0, ltcl_used = 0
        WHERE client_id = ? AND loss_year < ?
    """, (client_id, from_year))
    conn.commit()
    conn.close()


# ============================================================
# CONSOLIDATED PORTFOLIO
# ============================================================

def get_all_transactions_for_client(client_id):
    conn = sqlite3.connect(DB_NAME)
    c    = conn.cursor()
    c.execute("""
        SELECT date, type, company, isin, quantity, amount,
               buy_expenses, sell_expenses, stt, notes
        FROM transactions WHERE client_id = ?
        ORDER BY date ASC
    """, (client_id,))
    rows = c.fetchall()
    conn.close()
    return [
        {"date": r[0], "type": r[1], "company": r[2], "isin": r[3],
         "quantity": r[4], "amount": r[5],
         "buy_expenses": r[6], "sell_expenses": r[7],
         "stt": r[8], "notes": r[9]} for r in rows
    ]


# ============================================================
# BULK RENAME
# ============================================================

def bulk_rename_company(client_id, old_name, new_name, isin=None):
    if not old_name or not new_name:
        return 0, "Old and new names are required"
    if old_name.upper().strip() == new_name.upper().strip() and not isin:
        return 0, "No change"
    try:
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM transactions "
            "WHERE client_id = ? AND UPPER(TRIM(company)) = ?",
            (client_id, old_name.upper().strip())
        )
        count = cur.fetchone()[0]
        if count == 0:
            conn.close()
            return 0, None
        if isin:
            cur.execute("""
                UPDATE transactions
                SET company = ?,
                    isin = CASE
                        WHEN isin IS NULL OR TRIM(isin) = '' THEN ?
                        ELSE isin
                    END
                WHERE client_id = ? AND UPPER(TRIM(company)) = ?
            """, (new_name.upper().strip(), isin.upper().strip(),
                  client_id, old_name.upper().strip()))
        else:
            cur.execute("""
                UPDATE transactions SET company = ?
                WHERE client_id = ? AND UPPER(TRIM(company)) = ?
            """, (new_name.upper().strip(), client_id,
                  old_name.upper().strip()))
        conn.commit()
        rows_affected = cur.rowcount
        conn.close()
        return rows_affected, None
    except Exception as e:
        return 0, str(e)


def fill_missing_isin(client_id, company_name, isin):
    if not company_name or not isin:
        return 0, "Company name and ISIN are required"
    try:
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("""
            UPDATE transactions SET isin = ?
            WHERE client_id = ? AND UPPER(TRIM(company)) = ?
              AND (isin IS NULL OR TRIM(isin) = '')
        """, (isin.upper().strip(), client_id,
              company_name.upper().strip()))
        conn.commit()
        rows_affected = cur.rowcount
        conn.close()
        return rows_affected, None
    except Exception as e:
        return 0, str(e)


# ============================================================
# DAY 16 — MASTER SECURITIES DATABASE
# ============================================================

def add_security(isin, official_name, aliases=None, exchange=None):
    if aliases is None:
        aliases = []
    aliases = [a.upper().strip() for a in aliases if a.strip()]
    aliases_json = json.dumps(aliases)

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        INSERT INTO securities (isin, official_name, aliases, exchange)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(isin) DO UPDATE SET
            official_name = excluded.official_name,
            aliases = excluded.aliases,
            exchange = excluded.exchange,
            updated_at = CURRENT_TIMESTAMP
    """, (isin.upper().strip(), official_name.upper().strip(),
          aliases_json, exchange))
    conn.commit()
    conn.close()


def get_security_by_isin(isin):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        SELECT isin, official_name, aliases, exchange
        FROM securities WHERE isin = ?
    """, (isin.upper().strip(),))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "isin": row[0], "official_name": row[1],
        "aliases": json.loads(row[2] or "[]"), "exchange": row[3]
    }


def _split_to_words(text):
    cleaned = re.sub(r'[^A-Z0-9 ]', ' ', text.upper())
    return [w for w in cleaned.split() if w]


def search_securities_by_name(name):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        SELECT isin, official_name, aliases, exchange FROM securities
    """)
    rows = c.fetchall()
    conn.close()

    name_clean = name.upper().strip()
    name_word_clean = re.sub(r'[^A-Z0-9 ]', ' ', name_clean).strip()
    if not name_word_clean:
        return []

    matches = []
    seen_isins = set()

    for row in rows:
        isin, official_name, aliases_json, exchange = row
        aliases = json.loads(aliases_json or "[]")
        official_upper = official_name.upper()
        official_words = _split_to_words(official_name)
        match_type = None

        if name_clean in aliases:
            match_type = "alias_exact"
        elif name_clean == official_upper:
            match_type = "official_exact"
        elif len(name_word_clean) >= 4:
            typed_as_word = name_word_clean in official_words
            starts_with = False
            if len(name_word_clean) >= 5:
                starts_with = official_upper.startswith(name_word_clean + " ")
            if typed_as_word or starts_with:
                match_type = "official_word"

        if match_type and isin not in seen_isins:
            seen_isins.add(isin)
            matches.append({
                "isin": isin, "official_name": official_name,
                "match_type": match_type, "exchange": exchange
            })

    priority = {"alias_exact": 0, "official_exact": 1, "official_word": 2}
    matches.sort(key=lambda m: priority.get(m["match_type"], 99))
    return matches[:8]


def check_alias_conflict(alias):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    alias_clean = alias.upper().strip()
    c.execute("SELECT isin, aliases FROM securities")
    rows = c.fetchall()
    conn.close()
    for isin, aliases_json in rows:
        aliases = json.loads(aliases_json or "[]")
        if alias_clean in aliases:
            return isin
    return None


def add_alias_to_security(isin, alias):
    conflict_isin = check_alias_conflict(alias)
    if conflict_isin and conflict_isin != isin.upper().strip():
        return False, conflict_isin
    security = get_security_by_isin(isin)
    if not security:
        return False, "not found"
    aliases = security["aliases"]
    alias_clean = alias.upper().strip()
    if alias_clean not in aliases:
        aliases.append(alias_clean)
    add_security(isin=isin, official_name=security["official_name"],
                 aliases=aliases, exchange=security["exchange"])
    return True, None


def update_security(isin, official_name, aliases=None, exchange=None):
    add_security(isin, official_name, aliases, exchange)


def delete_security(isin):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM securities WHERE isin = ?",
              (isin.upper().strip(),))
    conn.commit()
    conn.close()


def get_all_securities(search=None, page=1, per_page=50):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        SELECT isin, official_name, aliases, exchange
        FROM securities ORDER BY official_name ASC
    """)
    rows = c.fetchall()
    conn.close()

    if search:
        search_clean = search.upper().strip()
        filtered = []
        for row in rows:
            isin, official_name, aliases_json, exchange = row
            aliases = json.loads(aliases_json or "[]")
            if (search_clean in isin or
                search_clean in official_name or
                any(search_clean in a for a in aliases)):
                filtered.append(row)
        rows = filtered

    total_count = len(rows)
    start = (page - 1) * per_page
    end = start + per_page
    rows = rows[start:end]

    result = []
    for row in rows:
        result.append({
            "isin": row[0], "official_name": row[1],
            "aliases": json.loads(row[2] or "[]"), "exchange": row[3]
        })
    return result, total_count


# ============================================================
# DAY 16 — FAST CACHE FOR RECONCILIATION (Phase 4 fix)
# ============================================================

def build_master_db_cache():
    """
    Load ENTIRE master DB into memory as fast lookup dicts.
    Call this ONCE at the start of a reconciliation run.

    Returns a dict with two lookup tables:
        {
            'by_alias': { "ABFRL": "INE647O01011", ... },
            'by_name_exact': { "RELIANCE INDUSTRIES LIMITED": "INE002A01018", ... },
            'name_index': [
                (isin, official_name_upper, set_of_words),
                ...
            ]
        }

    Lookups become O(1) instead of O(n).
    """
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT isin, official_name, aliases FROM securities")
    rows = c.fetchall()
    conn.close()

    by_alias = {}
    by_name_exact = {}
    name_index = []

    for isin, official_name, aliases_json in rows:
        official_upper = official_name.upper()
        by_name_exact[official_upper] = isin

        # Build word set for fast word-match lookup
        word_set = set(_split_to_words(official_name))
        name_index.append((isin, official_upper, word_set))

        # Aliases
        try:
            aliases = json.loads(aliases_json or "[]")
            for a in aliases:
                by_alias[a.upper().strip()] = isin
        except Exception:
            pass

    return {
        "by_alias": by_alias,
        "by_name_exact": by_name_exact,
        "name_index": name_index
    }


def resolve_isin_from_cache(name, given_isin, cache):
    """
    Fast in-memory lookup using prebuilt cache.

    Returns master DB ISIN if found, else None.

    Rules (same as search_securities_by_name but faster):
      1. ISIN given + exists in cache → return it
      2. Name in by_alias (exact) → return that ISIN
      3. Name in by_name_exact (exact) → return that ISIN
      4. For 4+ char names: check word-match in name_index
         - Only 1 match → return ISIN
         - 0 or 2+ matches → None (safe)
    """
    # Step 1: ISIN lookup
    if given_isin:
        given_isin = given_isin.strip().upper()
        if len(given_isin) == 12 and given_isin in cache["by_name_exact"].values():
            # Confirm it exists by checking name_index
            for isin, _, _ in cache["name_index"]:
                if isin == given_isin:
                    return given_isin

    # Step 2-4: Name lookup
    if not name:
        return None

    name_clean = name.upper().strip()
    name_word_clean = re.sub(r'[^A-Z0-9 ]', ' ', name_clean).strip()
    if not name_word_clean:
        return None

    # Alias exact (O(1))
    if name_clean in cache["by_alias"]:
        return cache["by_alias"][name_clean]

    # Official name exact (O(1))
    if name_clean in cache["by_name_exact"]:
        return cache["by_name_exact"][name_clean]

    # Word match — only for 4+ chars
    if len(name_word_clean) < 4:
        return None

    matches = []
    for isin, official_upper, word_set in cache["name_index"]:
        if name_word_clean in word_set:
            matches.append(isin)
        elif (len(name_word_clean) >= 5
              and official_upper.startswith(name_word_clean + " ")):
            matches.append(isin)

        if len(matches) > 1:
            # Ambiguous — stop early
            return None

    if len(matches) == 1:
        return matches[0]
    return None