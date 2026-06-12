"""
Debug script — shows what pypdf reads from a PDF.
Run: python test_pdf.py
"""

import pypdf
import re

# ⚠️ Change this to YOUR PDF's full path
PDF_PATH = r"C:\Users\Avyukt Gupta\Downloads\DPBT_10538594_NSDL_31052026_unlocked.pdf"

print("="*70)
print("PDF DEBUG TEST")
print("="*70)

try:
    reader = pypdf.PdfReader(PDF_PATH)
    print(f"\n✅ PDF opened. Pages: {len(reader.pages)}")

    # Print text from each page (first 500 chars only)
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        print(f"\n{'─'*70}")
        print(f"PAGE {i+1} — {len(text)} characters")
        print(f"{'─'*70}")
        print(text[:1500])  # First 1500 chars
        print("..." if len(text) > 1500 else "")

    # Count ISINs found in whole PDF
    all_text = ""
    for page in reader.pages:
        all_text += page.extract_text() + "\n"

    isins = re.findall(r'IN[EFA9][A-Z0-9]{9}', all_text)
    unique_isins = set(isins)

    print(f"\n{'='*70}")
    print(f"SUMMARY")
    print(f"{'='*70}")
    print(f"Total characters extracted: {len(all_text)}")
    print(f"ISINs found: {len(isins)} ({len(unique_isins)} unique)")

    if unique_isins:
        print(f"\nFirst 5 unique ISINs:")
        for isin in list(unique_isins)[:5]:
            print(f"  - {isin}")

    # Check for header words
    print(f"\nHeader keywords:")
    for keyword in ["Eureka", "EUREKA", "Holding as on", "Holdings as on",
                    "ISIN Code", "Free Bal", "Pldg Bal"]:
        found = keyword in all_text
        print(f"  {'✅' if found else '❌'} '{keyword}'")

except Exception as e:
    print(f"\n❌ Error: {e}")