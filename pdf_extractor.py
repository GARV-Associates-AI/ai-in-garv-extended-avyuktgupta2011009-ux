# pdf_extractor.py
# ============================================================
# Extracts transactions from PDF broker statements using AI
# UPDATED: Day 10 — STT extraction + auto-retry on 503 errors
# UPDATED: Day 12 — Fixed Amount extraction (use GROSS, not net)
# UPDATED: Day 12.5 — Safety verification + per-row warnings
# ============================================================

from google import genai
from config import GEMINI_API_KEY
from pypdf import PdfReader
import json
import re
import time

client_ai = genai.Client(api_key=GEMINI_API_KEY)
MODEL_NAME = "gemini-2.5-flash"


# ------------------------------------------------------------
# READ PDF TEXT
# ------------------------------------------------------------
def extract_pdf_text(file_storage):
    try:
        reader = PdfReader(file_storage)
        text = ""

        for page_num, page in enumerate(reader.pages, start=1):
            page_text = page.extract_text()
            if page_text:
                text += f"\n--- Page {page_num} ---\n{page_text}\n"

        return text, None

    except Exception as e:
        return None, f"Could not read PDF: {str(e)}"


# ------------------------------------------------------------
# AI PROMPT — Day 12.5 (with net payable extraction)
# ------------------------------------------------------------
EXTRACTION_PROMPT = """
You are an EXPERT Indian Chartered Accountant analyzing a stock broker
CONTRACT NOTE or transaction statement.

Your job: Extract every share transaction AND correctly assign:
  1. GROSS amount (BEFORE any brokerage/charges)
  2. Deductible expenses (brokerage etc.) per Section 48
  3. STT separately (NOT deductible per Section 40(a)(ib))
  4. The total NET PAYABLE/RECEIVABLE for verification

================================================================
STEP 1: IDENTIFY TRANSACTIONS
================================================================
For each equity share transaction in the PDF:

A) DETERMINE TYPE (BUY vs SELL):
   - If labeled "Sell" / "S" or column header is SELL → type = "SELL"
   - If labeled "Buy"  / "B" or column header is BUY  → type = "BUY"
   - If quantity is shown as NEGATIVE (-1000) → likely SELL
   - If it is a company tender offer / buyback → type = "BUYBACK"
   - If it is free shares from corporate action → type = "BONUS"

B) EXTRACT FOR EACH ROW:
   - date         (YYYY-MM-DD — find from PDF header if not in row)
   - type         (BUY / SELL / BUYBACK / BONUS)
   - company      (UPPERCASE — e.g. RELIANCE, WIPRO, JPPOWER)
   - isin         (e.g. INE002A01018, empty string if missing)
   - quantity     (ALWAYS POSITIVE integer — even if shown as -1000, use 1000)

================================================================
⚠️ CRITICAL — HOW TO EXTRACT "amount" (THE GROSS VALUE)
================================================================
The "amount" field MUST be the GROSS transaction value
BEFORE any brokerage / charges are added or deducted.

For each row, calculate amount as:
   amount = WAP (across exchanges) × Quantity

Where "WAP (across exchanges)" is the WEIGHTED AVERAGE PRICE
PER SHARE — the rate at which the trade actually executed,
BEFORE brokerage adjustment.

🚫 DO NOT USE:
   - "WAP (after brokerage)"
   - "Total BUY/SELL Value after brokerage"
   - "Net Value"
   - "Net Amount Receivable/Payable"
   - Any column labeled "after brokerage"

✅ USE:
   - "WAP (across exchanges)" × "Quantity Total"
   - OR if PDF only shows one rate column, use that × quantity
   - OR if PDF shows "Trade Value" / "Gross Value", use that

EXAMPLE — JPPOWER row:
   WAP (across exchanges)        = 22.9600
   Brokerage per Share           = 0.0500
   WAP (after brokerage)         = 22.9100   ← DO NOT USE
   Total SELL Value (after brok) = 22,910.00 ← DO NOT USE
   Quantity                      = 1000

   CORRECT amount = 22.9600 × 1000 = 22,960.00  ✅
   WRONG amount   = 22,910.00                    ❌

EXAMPLE — WIPRO row:
   WAP (across exchanges)        = 202.7014
   Quantity                      = 400

   CORRECT amount = 202.7014 × 400 = 81,080.56  ✅

================================================================
STEP 2: PROCESS THE "OBLIGATION DETAILS" / "CHARGES" SECTION
================================================================
At the BOTTOM of the contract note, find the charges section.
Categorize each line into ONE of two groups:

GROUP A — DEDUCTIBLE EXPENSES (per Section 48):
   - Brokerage / Taxable Value Of Supply (Brokerage)
   - Exchange Transaction Charges (TOC BSE / TOC NSE)
   - SEBI Turnover Tax / Fees (Sebi Tot)
   - CGST and SGST (or IGST)
   - Stamp Duty
   - Service Tax
   - Rounding (if shown — subtract if CR, add if DR)

GROUP B — STT (NOT deductible — track separately):
   - Securities Transaction Tax (STT)
   - Any line item explicitly labeled "STT"

CALCULATE:
   total_deductible = Sum of all Group A items
   total_stt        = Sum of all Group B items

⚠️ EXCLUDE non-charge items like "Pay In/Pay Out Obligation",
   "Net Amount", "Settlement Number", "Total Taxable Value"

================================================================
STEP 3: PRO-RATE EXPENSES AND STT BY GROSS TRANSACTION VALUE
================================================================
For each transaction:

   sum_of_amounts = sum of GROSS amount across all transactions
   expense_share  = total_deductible × (transaction_amount / sum_of_amounts)
   stt_share      = total_stt        × (transaction_amount / sum_of_amounts)

EXAMPLE — JPPOWER + WIPRO contract note:
   JPPOWER gross = 22,960.00     (22.06% of total)
   WIPRO   gross = 81,080.56     (77.94% of total)
   Total gross   = 1,04,040.56

   total_deductible = 268.44
   total_stt        = 104.00

   JPPOWER sell_expenses = 268.44 × 22.06% = 59.22  ✅
   JPPOWER stt           = 104.00 × 22.06% = 22.94  ✅
   WIPRO   buy_expenses  = 268.44 × 77.94% = 209.22 ✅
   WIPRO   stt           = 104.00 × 77.94% = 81.06  ✅

Assign based on type:
   - BUY     → buy_expenses=share, sell_expenses=0, stt=stt_share
   - SELL    → sell_expenses=share, buy_expenses=0, stt=stt_share
   - BUYBACK → sell_expenses=share, buy_expenses=0, stt=stt_share
   - BONUS   → all three (buy_expenses, sell_expenses, stt) = 0

================================================================
STEP 4: EXTRACT NET PAYABLE FOR VERIFICATION
================================================================
Find the FINAL net amount line at the bottom of the PDF.
Look for labels like:
   - "Net Amount Receivable/Payable By Client"
   - "Net Amount"
   - "Total Payable"
   - "Net Obligation"

Extract:
   - net_amount     : the rupee value (e.g. 58493.00)
   - net_direction  : "DR" (you pay) or "CR" (you receive)

If you cannot find it, set net_amount = 0 and net_direction = "".

================================================================
STEP 5: SKIP NON-TRANSACTION ROWS
================================================================
DO NOT extract:
   - Mutual fund / SIP / Bond / NCD / Debenture rows
   - F&O (Futures / Options) trades
   - Dividend / Interest payouts
   - Header / Total / Summary rows
   - Pay-in / Pay-out / Settlement rows

================================================================
OUTPUT FORMAT — RETURN A JSON OBJECT (NOT ARRAY)
================================================================
Return ONLY a valid JSON object with this structure:

{
  "transactions": [
    {
      "date": "2026-05-27",
      "type": "SELL",
      "company": "JPPOWER",
      "isin": "INE351F01018",
      "quantity": 1000,
      "amount": 22960.00,
      "buy_expenses": 0,
      "sell_expenses": 59.22,
      "stt": 22.94,
      "notes": "Pro-rated from contract note"
    },
    {
      "date": "2026-05-27",
      "type": "BUY",
      "company": "WIPRO",
      "isin": "INE075A01022",
      "quantity": 400,
      "amount": 81080.56,
      "buy_expenses": 209.22,
      "sell_expenses": 0,
      "stt": 81.06,
      "notes": "Pro-rated from contract note"
    }
  ],
  "verification": {
    "net_amount": 58493.00,
    "net_direction": "DR",
    "total_charges_excl_stt": 268.44,
    "total_stt": 104.00
  }
}

If NO transactions found, return:
{"transactions": [], "verification": {"net_amount": 0, "net_direction": "", "total_charges_excl_stt": 0, "total_stt": 0}}

================================================================
PDF CONTENT TO ANALYZE:
================================================================
"""


# ------------------------------------------------------------
# AI CALL WITH AUTO-RETRY
# ------------------------------------------------------------
def call_ai_with_retry(full_prompt, max_retries=3):
    wait_times = [3, 6, 10]
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            print(f"🤖 AI call attempt {attempt}/{max_retries}...")

            response = client_ai.models.generate_content(
                model=MODEL_NAME,
                contents=full_prompt
            )

            return response.text.strip(), None

        except Exception as e:
            error_str = str(e)
            last_error = error_str

            is_overloaded = (
                "503" in error_str
                or "UNAVAILABLE" in error_str
                or "overload" in error_str.lower()
                or "high demand" in error_str.lower()
                or "rate limit" in error_str.lower()
                or "429" in error_str
            )

            if is_overloaded and attempt < max_retries:
                wait = wait_times[attempt - 1]
                print(f"⏳ Gemini is busy. Waiting {wait}s before retry...")
                time.sleep(wait)
                continue
            else:
                break

    if last_error and ("503" in last_error or "UNAVAILABLE" in last_error
                       or "high demand" in last_error.lower()):
        return None, (
            "⏳ Gemini AI is currently overloaded. "
            "We tried 3 times automatically but it didn't work. "
            "Please wait 1-2 minutes and try uploading the PDF again."
        )

    return None, f"AI extraction failed: {last_error}"


# ------------------------------------------------------------
# MAIN EXTRACTION FUNCTION
# ------------------------------------------------------------
def extract_transactions_from_pdf(file_storage):
    """
    Returns: (transactions_list, verification_dict, error_message)
    verification_dict has: net_amount, net_direction, ai_total_calculated,
                           difference, match_status, warnings
    """

    pdf_text, error = extract_pdf_text(file_storage)
    if error:
        return [], None, error

    if not pdf_text or len(pdf_text.strip()) < 50:
        return [], None, "PDF appears to be empty or unreadable."

    if len(pdf_text) > 30000:
        pdf_text = pdf_text[:30000] + "\n\n[NOTE: PDF was truncated due to size]"

    full_prompt = EXTRACTION_PROMPT + "\n" + pdf_text

    ai_response, error = call_ai_with_retry(full_prompt, max_retries=3)
    if error:
        return [], None, error

    transactions, verification, parse_error = parse_ai_response(ai_response)

    if parse_error:
        return [], None, parse_error

    valid_transactions = []
    for tx in transactions:
        if validate_transaction(tx):
            valid_transactions.append(tx)

    if not valid_transactions:
        return [], None, "AI could not find any valid transactions in this PDF."

    # Add per-row warnings
    valid_transactions = add_row_warnings(valid_transactions)

    # Fix expense assignment if needed
    valid_transactions = verify_and_fix_expenses(valid_transactions)

    # Build verification report
    verification_report = build_verification_report(
        valid_transactions, verification
    )

    return valid_transactions, verification_report, None


# ------------------------------------------------------------
# PER-ROW WARNING DETECTION
# ------------------------------------------------------------
def add_row_warnings(transactions):
    """
    Adds a 'warnings' list to each transaction with risk indicators.
    """
    for tx in transactions:
        try:
            warnings = []
            amount   = float(tx.get("amount", 0) or 0)
            buy_exp  = float(tx.get("buy_expenses", 0) or 0)
            sell_exp = float(tx.get("sell_expenses", 0) or 0)
            stt      = float(tx.get("stt", 0) or 0)
            isin     = str(tx.get("isin", "") or "").strip()
            tx_type  = str(tx.get("type", "")).upper()

            total_exp = buy_exp + sell_exp

            # Check 1: Expense ratio too high
            if amount > 0:
                exp_pct = (total_exp / amount) * 100
                if exp_pct > 1.0:
                    warnings.append(
                        f"⚠️ Expenses are {exp_pct:.2f}% of amount "
                        f"(usually 0.05–0.20%). Verify."
                    )

            # Check 2: STT ratio suspicious
            if amount > 0 and stt > 0:
                stt_pct = (stt / amount) * 100
                if stt_pct > 0.15:
                    warnings.append(
                        f"⚠️ STT is {stt_pct:.3f}% of amount "
                        f"(usually 0.1%). Verify."
                    )

            # Check 3: STT on BUY side (unusual for delivery)
            if tx_type == "BUY" and stt > 0:
                warnings.append(
                    "ℹ️ STT charged on BUY side — verify if delivery trade"
                )

            # Check 4: Missing ISIN for big trade
            if amount > 50000 and not isin:
                warnings.append("⚠️ ISIN missing for a large trade")

            # Check 5: Suspiciously round amount
            if amount > 0 and amount == int(amount) and amount % 1000 == 0:
                warnings.append(
                    "ℹ️ Round amount — verify against PDF"
                )

            # Check 6: BONUS with non-zero values
            if tx_type == "BONUS":
                if amount > 0 or total_exp > 0 or stt > 0:
                    warnings.append(
                        "⚠️ BONUS should have 0 cost — values being reset"
                    )

            tx["warnings"]    = warnings
            tx["risk_level"]  = (
                "high"   if any("⚠️" in w for w in warnings) else
                "medium" if warnings else
                "low"
            )

        except Exception:
            tx["warnings"]   = []
            tx["risk_level"] = "low"

    return transactions


# ------------------------------------------------------------
# VERIFICATION REPORT BUILDER
# ------------------------------------------------------------
def build_verification_report(transactions, ai_verification):
    """
    Compares AI-extracted totals against PDF's stated net amount.
    Returns a dict the review page can display.
    """
    if not ai_verification:
        ai_verification = {}

    pdf_net       = float(ai_verification.get("net_amount", 0) or 0)
    pdf_direction = ai_verification.get("net_direction", "") or ""

    # Calculate AI's net total
    buy_total  = 0.0
    sell_total = 0.0

    for tx in transactions:
        amount   = float(tx.get("amount", 0) or 0)
        buy_exp  = float(tx.get("buy_expenses", 0) or 0)
        sell_exp = float(tx.get("sell_expenses", 0) or 0)
        stt      = float(tx.get("stt", 0) or 0)
        tx_type  = str(tx.get("type", "")).upper()

        if tx_type == "BUY":
            # For BUY: client pays gross + expenses + STT
            buy_total += amount + buy_exp + stt
        elif tx_type in ["SELL", "BUYBACK"]:
            # For SELL: client receives gross - expenses - STT
            sell_total += amount - sell_exp - stt

    # Net = what client pays (positive) or receives (negative)
    ai_net = buy_total - sell_total

    # Compare with PDF
    if pdf_net > 0:
        difference = abs(abs(ai_net) - pdf_net)

        if difference <= 5:
            match_status = "match"
            status_message = (
                f"✅ Math verified — AI total matches PDF "
                f"(difference: ₹{difference:.2f})"
            )
        elif difference <= 50:
            match_status = "minor_mismatch"
            status_message = (
                f"🟡 Small mismatch of ₹{difference:.2f} — "
                f"possibly rounding. Please verify."
            )
        else:
            match_status = "mismatch"
            status_message = (
                f"🔴 WARNING: AI total differs from PDF by ₹{difference:.2f}! "
                f"Please verify each row carefully before importing."
            )
    else:
        difference = 0
        match_status = "no_pdf_total"
        status_message = (
            "ℹ️ Could not find 'Net Amount' in PDF. "
            "Please verify each row manually."
        )

    return {
        "pdf_net_amount"      : round(pdf_net, 2),
        "pdf_net_direction"   : pdf_direction,
        "ai_net_amount"       : round(abs(ai_net), 2),
        "ai_direction"        : "DR" if ai_net > 0 else "CR",
        "difference"          : round(difference, 2),
        "match_status"        : match_status,
        "status_message"      : status_message,
        "total_charges_excl_stt": round(
            float(ai_verification.get("total_charges_excl_stt", 0) or 0), 2
        ),
        "total_stt"           : round(
            float(ai_verification.get("total_stt", 0) or 0), 2
        ),
    }


# ------------------------------------------------------------
# VERIFY EXPENSE PRO-RATION + STT (existing logic)
# ------------------------------------------------------------
def verify_and_fix_expenses(transactions):
    for tx in transactions:
        try:
            tx_type        = str(tx.get("type", "")).upper()
            buy_exp        = float(tx.get("buy_expenses", 0) or 0)
            sell_exp       = float(tx.get("sell_expenses", 0) or 0)
            stt            = float(tx.get("stt", 0) or 0)
            existing_notes = tx.get("notes", "") or ""

            if tx_type == "BUY" and sell_exp > 0:
                tx["buy_expenses"]  = buy_exp + sell_exp
                tx["sell_expenses"] = 0
                tx["notes"] = (
                    existing_notes
                    + " | Auto-fixed: moved sell exp to buy"
                ).strip(" |")

            if tx_type in ["SELL", "BUYBACK"] and buy_exp > 0:
                tx["sell_expenses"] = sell_exp + buy_exp
                tx["buy_expenses"]  = 0
                tx["notes"] = (
                    existing_notes
                    + " | Auto-fixed: moved buy exp to sell"
                ).strip(" |")

            if tx_type == "BONUS":
                tx["buy_expenses"]  = 0
                tx["sell_expenses"] = 0
                tx["stt"]           = 0

            if stt < 0:
                tx["stt"] = 0

        except Exception:
            continue

    return transactions


# ------------------------------------------------------------
# PARSE AI'S JSON RESPONSE
# ------------------------------------------------------------
def parse_ai_response(text):
    """
    Now parses an OBJECT (not array) with 'transactions' and 'verification'.
    Returns: (transactions, verification, error)
    """
    text = re.sub(r'^```json\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^```\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE)
    text = text.strip()

    # Try to find JSON object
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        text = match.group(0)

    try:
        data = json.loads(text)

        # Handle both old format (array) and new format (object)
        if isinstance(data, list):
            # Old format — array of transactions, no verification
            return data, {}, None

        if isinstance(data, dict):
            transactions = data.get("transactions", [])
            verification = data.get("verification", {})

            if not isinstance(transactions, list):
                return [], {}, "AI returned unexpected format"

            return transactions, verification, None

        return [], {}, "AI returned unexpected format"

    except json.JSONDecodeError as e:
        return [], {}, f"Could not parse AI response: {str(e)}"


# ------------------------------------------------------------
# VALIDATE EACH TRANSACTION
# ------------------------------------------------------------
def validate_transaction(tx):
    required = ["date", "type", "company", "quantity", "amount"]

    for field in required:
        if field not in tx:
            return False

    tx_type = str(tx["type"]).upper().strip()
    if tx_type not in ["BUY", "SELL", "BUYBACK", "BONUS"]:
        return False

    try:
        qty = int(tx["quantity"])
        if qty <= 0:
            return False
    except (ValueError, TypeError):
        return False

    try:
        amt = float(tx["amount"])
        if tx_type != "BONUS" and amt <= 0:
            return False
        if amt < 0:
            return False
    except (ValueError, TypeError):
        return False

    if "stt" not in tx:
        tx["stt"] = 0
    else:
        try:
            tx["stt"] = float(tx.get("stt", 0) or 0)
            if tx["stt"] < 0:
                tx["stt"] = 0
        except (ValueError, TypeError):
            tx["stt"] = 0

    date_str = str(tx["date"]).strip()
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return False

    if not str(tx.get("company", "")).strip():
        return False

    return True