# Sample Q&A (tested)

These answers were generated against the committed index on **2026-11-05** using
Groq `qwen/qwen3.8-27b` and the retrieval path described in `architecture.md`.
Each answer is the actual application output (no hand-written facts). The
`Last updated from sources` date is the maximum `fetched_at` of the retrieved
chunks, not the time the question was asked.

---

### 1. Expense ratio — answered

**Question:** What is the expense ratio of HDFC Large Cap Fund Direct Growth?

**Answer:** The expense ratio of HDFC Large Cap Fund Direct Growth is 1.02%.

**Source:** https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth

**Last updated from sources:** 2026-11-05

**Status:** Answered

---

### 2. Exit load — answered

**Question:** What is the exit load of HDFC Mid Cap Fund Direct Growth?

**Answer:** The exit load for HDFC Mid Cap Fund Direct Growth is 1% if redeemed within 1 year.

**Source:** https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth

**Last updated from sources:** 2026-11-05

**Status:** Answered

---

### 3. ELSS lock-in — answered

**Question:** What is the lock-in period for HDFC ELSS Tax Saver Fund?

**Answer:** The lock-in period for the HDFC ELSS Tax Saver Fund is 3 years.

**Source:** https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth

**Last updated from sources:** 2026-11-05

**Status:** Answered

---

### 4. Minimum SIP — answered

**Question:** What is the minimum SIP for HDFC Flexi Cap Fund?

**Answer:** The minimum SIP for HDFC Flexi Cap Fund is ₹100.

**Source:** https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth

**Last updated from sources:** 2026-11-05

**Status:** Answered

---

### 5. Riskometer — answered

**Question:** What is the riskometer rating of HDFC Large Cap Fund Direct Growth?

**Answer:** The riskometer rating for HDFC Large Cap Fund Direct Growth is Very High Risk.

**Source:** https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth

**Last updated from sources:** 2026-11-05

**Status:** Answered

---

### 6. Capital-gains statement — answered

**Question:** How do I download a capital gains statement for my mutual funds?

**Answer:** You can download a capital gains statement by logging into your mutual fund company's website or the CAMS portal (camsonline.com) and navigating to the statements section. Alternatively, you can access the "Capital Gain/Loss Statement" option on the KFintech portal or through the "Reports" section of online investment platforms.

**Source:** https://www.mutualfundssahihai.com/en/how-get-capital-gain-statement-mutual-funds

**Last updated from sources:** 2026-11-05

**Status:** Answered

---

### 7. Section 80C tax proof — answered

**Question:** Where can I get tax proof for Section 80C?

**Answer:** You can request your tax proof for Section 80C by downloading the Mutual Fund-ELSS statement. To do this, go to your profile, select 'Reports', and under 'Tax', choose 'Mutual Fund - Capital Gains' for the desired financial year.

**Source:** https://groww.in/help/my-account/ma-others/where-can-i-get-the-tax-proof-for-section-80c

**Last updated from sources:** 2026-11-05

**Status:** Answered

---

### 8. Factsheet — answered

**Question:** What is a mutual fund factsheet?

**Answer:** A mutual fund factsheet is a reliable guide that provides detailed information on a scheme, including its investment objective, benchmark, AUM, fund managers, and available plans. It also covers performance and risk parameters such as standard deviation, beta, Sharpe ratio, and expense ratio, along with portfolio holdings and historic performance compared to the benchmark.

**Source:** https://www.mutualfundssahihai.com/en/what-factsheet

**Last updated from sources:** 2026-11-05

**Status:** Answered

---

### 9. Investment advice — refused

**Question:** Should I buy HDFC Mid Cap Fund?

**Answer:** I can only share facts from official HDFC Mutual Fund pages and can't give investment advice, recommendations, or portfolio guidance. For general investor education, see the official page linked here.

**Source:** https://investor.sebi.gov.in/elss.html

**Last updated from sources:** N/A (refusal)

**Status:** Refused — advice

---

### 10. Out-of-corpus AMC — refused

**Question:** What is the expense ratio of SBI Bluechip Fund?

**Answer:** I can only answer from the HDFC Mutual Fund pages in this demo's sources. For other AMCs or account actions, please use the official page linked here.

**Source:** https://groww.in/mutual-funds/amc/hdfc-mutual-funds

**Last updated from sources:** N/A (refusal)

**Status:** Refused — out of corpus

---

## Notes

- Every answered example has exactly one source link and no more than three
  sentences.
- Refusals are deterministic and do **not** call the LLM or retrieval.
- PII questions are refused before retrieval and are never written to
  `data/retrieval/`; see `tests/test_safety_gate.py`.
- Retrieval traces for tested questions are inspectable in `data/retrieval/` and
  in the admin dashboard (requires `ADMIN_PASSCODE`).
