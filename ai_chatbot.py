# ai_chatbot.py
# ============================================================
# AI Chatbot powered by Google Gemini (NEW SDK)
# Understands the client's transactions and tax position
# ============================================================

from google import genai
from config import GEMINI_API_KEY
from calculator import run_fifo, calculate_tax_summary
from database import get_transactions, get_fmv, get_client

# Create Gemini client once
client_ai = genai.Client(api_key=GEMINI_API_KEY)

# Model to use (free + fast)
MODEL_NAME = "gemini-2.5-flash"


# ------------------------------------------------------------
# SYSTEM PROMPT
# ------------------------------------------------------------
SYSTEM_PROMPT = """
You are a Capital Gains Tax expert assistant for a Chartered Accountant in India.
You help analyze share transactions and explain tax positions.

IMPORTANT RULES YOU MUST FOLLOW:
- All rates are effective from 1 April 2026
- LTCG (held >= 365 days) tax rate: 12.5%
- STCG (held < 365 days) tax rate: 20%
- LTCG exemption under Section 112A: Rs 1,25,000
- Health & Education Cess: 4% on tax
- Buyback is treated as CAPITAL GAINS (NOT deemed dividend)
- STT is NOT deductible
- Buy expenses ADD to cost of acquisition
- Sell expenses REDUCE sale price
- Grandfathering date: 31-Jan-2018 (for shares bought before this date)
- FIFO method is mandatory for demat accounts
- STCL can offset both STCG and LTCG
- LTCL can ONLY offset LTCG
- Losses can be carried forward for 8 Assessment Years

HOW TO RESPOND:
- Be concise and professional
- Use Indian Rupee symbol Rs
- Format numbers with commas (e.g. Rs 1,25,000)
- Use bullet points for clarity
- If client data is provided, base answers on actual numbers
- For "what if" questions, explain the calculation
- For tax law questions, cite the relevant section
- DO NOT invent transactions or numbers
- If you don't have data, say so clearly
"""


# ------------------------------------------------------------
# BUILD CLIENT CONTEXT
# ------------------------------------------------------------
def build_client_context(client_id, fin_year):
    """
    Creates a text summary of client's tax position
    that the AI can use to answer questions.
    """

    client = get_client(client_id)
    if not client:
        return "No client data available."

    transactions = get_transactions(client_id, fin_year)
    fmv_data     = get_fmv(client_id)

    context = f"""
CLIENT INFORMATION:
- Name: {client[1]}
- PAN: {client[2]}
- Financial Year: {fin_year}
- Total transactions: {len(transactions)}

"""

    if not transactions:
        context += "No transactions entered yet for this year.\n"
        return context

    # Add transaction list
    context += "TRANSACTIONS:\n"
    for t in transactions:
        context += (
            f"- {t['date']} | {t['type']} | {t['company']} | "
            f"Qty: {t['quantity']} | "
            f"Amount: Rs {t['amount']:,.2f}"
        )
        if t['buy_expenses']:
            context += f" | Buy Exp: Rs {t['buy_expenses']:,.2f}"
        if t['sell_expenses']:
            context += f" | Sell Exp: Rs {t['sell_expenses']:,.2f}"
        context += "\n"

    # Add FMV data if available
    if fmv_data:
        context += "\nFMV ON 31-JAN-2018 (for grandfathering):\n"
        for company, fmv in fmv_data.items():
            context += f"- {company}: Rs {fmv:,.2f}\n"

    # Run calculation if any sells exist
    sells = [t for t in transactions if t['type'] in ['SELL', 'BUYBACK']]

    if sells:
        try:
            output_rows, errors = run_fifo(transactions, fmv_data)
            tax = calculate_tax_summary(output_rows)

            context += f"""
TAX POSITION (Calculated):
- Gross LTCG: Rs {tax['gross_ltcg']:,.2f}
- Gross LTCL: Rs {tax['gross_ltcl']:,.2f}
- Gross STCG: Rs {tax['gross_stcg']:,.2f}
- Gross STCL: Rs {tax['gross_stcl']:,.2f}

- Net LTCG (after set-off): Rs {tax['net_ltcg']:,.2f}
- Net STCG (after set-off): Rs {tax['net_stcg']:,.2f}

- LTCG Exemption u/s 112A: Rs {tax['ltcg_exemption']:,.2f}
- Taxable LTCG: Rs {tax['taxable_ltcg']:,.2f}
- Taxable STCG: Rs {tax['taxable_stcg']:,.2f}

- LTCG Tax @ 12.5%: Rs {tax['ltcg_tax']:,.2f}
- STCG Tax @ 20%: Rs {tax['stcg_tax']:,.2f}
- Total Tax: Rs {tax['total_tax']:,.2f}
- Cess @ 4%: Rs {tax['cess']:,.2f}
- TOTAL TAX LIABILITY: Rs {tax['grand_total']:,.2f}

- LTCL to carry forward: Rs {tax['ltcl_carryforward']:,.2f}
- STCL to carry forward: Rs {tax['stcl_carryforward']:,.2f}
"""

            # Add per-lot detail (limit to 20)
            if output_rows:
                context += "\nMATCHED LOTS (FIFO):\n"
                for row in output_rows[:20]:
                    context += (
                        f"- {row['company']} | Bought {row['buy_date']} | "
                        f"Sold {row['sell_date']} | Qty {row['shares']} | "
                        f"P/L Rs {row['profit_loss']:,.2f} | {row['gain_type']}\n"
                    )
                if len(output_rows) > 20:
                    context += f"... and {len(output_rows) - 20} more lots\n"

        except Exception as e:
            context += f"\n[Calculation error: {str(e)}]\n"

    return context


# ------------------------------------------------------------
# MAIN CHAT FUNCTION
# ------------------------------------------------------------
def ask_ai(question, client_id=None, fin_year="2024-25"):
    """
    Sends a question to Gemini and returns the answer.
    """

    try:
        # Build the full prompt
        full_prompt = SYSTEM_PROMPT

        if client_id:
            client_context = build_client_context(client_id, fin_year)
            full_prompt += f"\n\nCURRENT CLIENT DATA:\n{client_context}\n"

        full_prompt += f"\n\nUSER QUESTION: {question}\n\nANSWER:"

        # Call Gemini using new SDK
        response = client_ai.models.generate_content(
            model=MODEL_NAME,
            contents=full_prompt
        )

        return response.text

    except Exception as e:
        return (
            f"AI Error: {str(e)}\n\n"
            "Make sure your Gemini API key is correct in config.py"
        )


# ------------------------------------------------------------
# QUICK SUGGESTIONS
# ------------------------------------------------------------
def get_suggestions(has_data=False):
    """Returns suggested questions based on whether client has data"""
    if has_data:
        return [
            "What is my total tax liability?",
            "Which transactions are LTCG vs STCG?",
            "How much can I save if I wait for LTCG?",
            "Explain my grandfathering calculation",
            "Should I do tax loss harvesting?",
            "What losses can I carry forward?",
        ]
    else:
        return [
            "Explain FIFO method in simple terms",
            "What is grandfathering under Section 112A?",
            "How is buyback taxed after 1 Apr 2026?",
            "What is the difference between LTCG and STCG?",
            "Are brokerage charges deductible?",
            "How long can I carry forward losses?",
        ]