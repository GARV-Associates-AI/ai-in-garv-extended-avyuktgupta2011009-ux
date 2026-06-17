[![Open in Visual Studio Code](https://classroom.github.com/assets/open-in-vscode-2e0aaae1b6195c2367325f4f02e2d04e9abb55f0b24a779b69b11b9e10269abc.svg)](https://classroom.github.com/online_ide?assignment_repo_id=24125700&assignment_repo_type=AssignmentRepo)
## 🏦 Capital Gains AI Tool

**Tech Stack:** Python (Flask) · SQLite · Google Gemini AI · Chart.js · OpenPyXL

---

### What it is:
A comprehensive web-based Capital Gains Tax Calculator built for CA firms to manage multiple clients' share transaction data, compute tax liability accurately, generate ITR-ready reports and reconcile broker/government data — all as per Indian Income Tax rules effective 1 April 2026.

---

## 📌 Feature Breakdown

---

### 👥 Client Management
- Add unlimited clients with Name, PAN, Email, Phone
- Individual client dashboard with financial year selector
- Edit client details with PAN change confirmation
- Per-client password protection (set / change / remove / lock)
- Auto-lock clients after session ends
- Delete client with all associated data

---

### 📋 Transaction Management
- **7 transaction types supported:**
  - BUY, SELL, BUYBACK (post 1-Apr-2026 taxed as CG)
  - BONUS (zero cost per Sec 55)
  - SPLIT (auto-adjusts buy queue quantities)
  - GIFT (donor's cost & date adopted per Sec 49(1))
  - INHERIT (predecessor's cost & date adopted per Sec 49(1))
- Fields: Date, Type, Company, ISIN, Quantity, Amount, Buy Expenses, Sell Expenses, STT, Notes
- Edit and delete individual transactions
- Financial year wise transaction view
- ISIN auto-fill from previous entries
- Similar company name detection (Levenshtein distance algorithm)
- Master DB autocomplete (5,000+ securities) with CLIENT and MASTER badges

---

### 📥 Data Import
- **Manual entry** — form-based, one transaction at a time
- **Excel import** — bulk upload via structured template
  - Download template → fill → upload → review → confirm
  - Warnings shown separately from errors
- **AI PDF import** — upload broker contract notes
  - Google Gemini AI extracts all transactions automatically
  - Supports Zerodha, Groww, ICICI, and most brokers
  - Math verification banner (checks extracted total vs PDF total)
  - Review page before saving — edit any row before confirming
  - STT auto-extracted from contract notes

---

### ⚖️ Capital Gains Calculation (FIFO Engine)
- Strict FIFO matching of buy lots to sell transactions
- **LTCG** (Long Term) — held > 12 months → taxed @ 12.5%
- **STCG** (Short Term) — held ≤ 12 months → taxed @ 20%
- Health & Education Cess @ 4% on total tax
- LTCG exemption of ₹1,25,000 under Section 112A
- **Section 112A Grandfathering:**
  - Pre-31-Jan-2018 purchases use FMV as deemed cost
  - Grandfathered cost = max(actual cost, min(FMV, sale price))
  - GF badge shown on every applicable row
- STT tracked per transaction (informational — NOT deductible per Sec 40(a)(ib))
- Buy and sell expenses tracked and adjusted in cost/proceeds
- **Date-range filter** — calculate CG for specific sell date range
- Oversold detection with clear error messages
- FMV warning before calculation if pre-2018 shares missing FMV

---

### 📉 Brought Forward Loss Management
- Manual entry of losses from previous years
- Auto-save losses generated from each calculation
- **Set-off rules followed strictly:**
  - STCL set off against STCG first, then LTCG
  - LTCL set off only against LTCG
  - FIFO order (oldest loss first)
- 8-year expiry tracking with years-remaining display
- Source tracking (manual vs auto-saved)
- Loss application log shown on result page
- One-click "Apply B/F Losses & Recalculate" button
- Remaining losses shown as carry-forward for next year

---

### 📊 FMV (Fair Market Value) Management
- Required for pre-31-Jan-2018 shares under Sec 112A
- Single company FMV entry
- Bulk FMV import via Excel template
- NSE and BSE FMV reference files (31-Jan-2018) downloadable
- FMV adjustment rules guide built into the UI
- FMV warning banner on client page listing missing companies

---

### 📈 Reports & Analytics

**Capital Gains Result Page:**
- Summary cards — Gross LTCG, Gross LTCL, Gross STCG, Gross STCL, Net figures, Total STT
- Full tax computation breakdown with cess
- Transaction-wise FIFO matched lots table (14 columns)
- Grandfathering details per row
- STT column highlighted separately
- Carry-forward loss summary
- Print / PDF button (print-friendly layout)
- **Visual Charts (Chart.js):**
  - 🍩 Donut chart — STCG vs LTCG split
  - 📊 Horizontal bar chart — Top 5 winners & Top 5 losers by company
  - 📈 Line chart — Monthly gains timeline (April to March, FY-wise)

**Closing Stock / Portfolio:**
- Unsold holdings as on any date
- Three views: Year-end (31-Mar) / Today / Custom date
- Per-lot breakdown with cost, GF cost, holding period, LTCG/STCG status
- Summary cards per view (companies, shares, cost, LTCG/STCG split)
- Pre-2018 lots flagged with FMV needed warning
- Bonus, Gift, Inherit lots clearly labelled

**Consolidated Portfolio Report:**
- Multi-client selection
- Two layouts:
  - Combined date (all clients merged)
  - Per-client date (separate holdings per client)
- Excel export (professional, B&W, print-ready)

**ITR Schedule CG Generator:**
- Auto-generates Schedule CG from calculation data
- STCG → Section 111A | LTCG → Section 112A
- B/F loss set-off summary included
- Two Excel download options:
  - Detailed (transaction-wise)
  - Adjusted (ITR filing ready)
- 4 Excel sheets: STCG-111A · LTCG-112A · Summary · Filing Notes
- Print button with print-friendly CSS

---

### 🔍 Reconciliation Tools

**DP Statement Reconciliation:**
- Upload NSDL / CDSL DP statement PDFs
- Multi-PDF upload (NSDL + CDSL combined automatically)
- Smart rules-based parser for Eureka broker format
- Google Gemini AI fallback for other broker formats
- Manual Excel fallback (always works)
- Compares DP holdings vs app's calculated closing stock
- **5-tier matching:** ISIN → Exact Name → Master DB → Fuzzy 70%+ → Ambiguous
- Ambiguous matches handled via user-choice radio buttons
- Shows: Matched / Quantity Mismatch / Only in DP / Only in App
- Excel export (5 sheets)
- Auto-detects Bill Date from PDF

**AIS Reconciliation:**
- Upload AIS PDF (Annual Information Statement from IT portal)
- Rules-based parser — no AI needed
- Reads SFT-17-LES, SFT-17-EMF, SFT-17(Pur) sections
- Compares AIS transactions vs app transactions for selected FY
- **5-tier matching:** ISIN → Exact Name → Acronym → Substring → Fuzzy 70%+
- Auto-accept for ISIN / exact / single-candidate matches
- Quantity and value difference columns (App − AIS)
- Auto-fills missing ISINs from AIS data into app transactions
- Excel export (5 sheets)

---

### 📚 Master Securities Database
- Global database of 5,000+ securities (BSE loaded)
- Fields: ISIN (primary key), Official Name, Aliases, Exchange
- **CSV/Excel bulk import** — auto-detects NSE or BSE format
- Browse page with pagination (50 per page) and search
- Manual Add / Edit / Delete via modals
- **Integrated into autocomplete** — shows Master DB suggestions alongside client data
- **Integrated into PDF/Excel review** — suggests correct name/ISIN for imported transactions
- **Integrated into reconciliation** — used as Tier 2.5 matching layer
- Alias collision detection — prevents duplicate aliases across securities
- Save aliases directly from reconciliation results

**Standardize Company Names:**
- Bulk rename company names across all client transactions
- Auto-fill missing ISINs during rename
- Save aliases to Master DB with one click
- Bulk check/uncheck for Master DB saves

---

### 🤖 AI Chatbot
- Powered by Google Gemini
- Answers Indian capital gains tax queries
- Context-aware (knows client's transactions if opened from client page)
- Suggested questions for quick access
- Available globally (no client context needed too)

---

### 🔐 Security
- Per-client password protection with bcrypt hashing
- Session-based unlock (stays unlocked until browser closes or manual lock)
- Lock Now button per client
- Consolidated report requires all selected clients to be unlocked
- API keys stored in `.env` file (never hardcoded)
- `.gitignore` protects sensitive files
- `.env.example` template for safe sharing

---

### 💡 Smart UX Features
- Financial year selector (2021-22 to 2026-27)
- Tabbed right panel (Add / Excel / AI PDF / FMV)
- Type hints shown when selecting transaction type
- STT informational hint on entry form
- Flash messages for every action (success / error / warning)
- Collapsible B/F losses panel
- Breadcrumb navigation throughout
- All tables horizontally scrollable on small screens
- Hard refresh not needed — Flask restarts reflect immediately
