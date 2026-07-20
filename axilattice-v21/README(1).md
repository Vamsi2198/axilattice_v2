# Axilattice Sample Datasets

Two realistic datasets to test the engine end-to-end. Both have proper daily
time columns, multiple cube-worthy dimensions, at least one high-cardinality
dimension (correctly excluded), identifier columns (correctly excluded), real
numeric measures, intentional missing data, and embedded signals for the agent
to discover.

Verified against the actual profiler — every column maps as intended.

---

## 1. `ecommerce_orders.csv` — 57,643 rows

Order-level e-commerce transactions, daily across all of 2024.

| Column       | Profiler type      | Role in cube                    |
|--------------|--------------------|---------------------------------|
| order_id     | identifier         | excluded (unique key)           |
| order_date   | **temporal**       | time axis (day→week→month→qtr→yr)|
| region       | dimension (5)      | cube dimension                  |
| category     | dimension (6)      | cube dimension                  |
| channel      | dimension (4)      | cube dimension                  |
| segment      | dimension (3)      | cube dimension                  |
| payment      | dimension (4)      | cube dimension                  |
| city         | dim_high_card (80) | **excluded** (> cutoff 50)      |
| quantity     | dimension (4)      | cube dimension (coded numeric)  |
| revenue      | **measure**        | primary measure                 |
| margin_pct   | **measure**        | measure                         |
| discount     | **measure**        | measure                         |
| ship_days    | dimension (6)      | cube dimension (coded numeric)  |

**Missing data:** ~3% missing city, ~2% missing segment, ~1% missing revenue.

**Embedded signals:**
- Electronics revenue **craters ~45% in September** (supply crunch) — the agent
  should isolate `category=Electronics` at `2024-09` when asked "why did revenue drop."
- Festive spike in Oct–Nov (+45%), holiday lift in Dec (+25%), summer lull Jun–Jul.
- Weekend orders run ~20% higher than weekdays.

**Try:**
- "Revenue by region this month" → fast path, instant
- "Top 5 categories by revenue" → fast path
- "Monthly revenue trend" → fast path, shows festive spike
- "Why did electronics revenue drop?" → agent OODA loop finds September

---

## 2. `quickcommerce_deliveries.csv` — 79,346 rows

Delivery-level quick-commerce (sub-30-min) events, daily across 2024.

| Column       | Profiler type   | Role in cube                    |
|--------------|-----------------|---------------------------------|
| delivery_id  | identifier      | excluded (unique key)           |
| order_date   | **temporal**    | time axis                       |
| city         | dimension (6)   | cube dimension                  |
| dark_store   | dimension (8)   | cube dimension                  |
| category     | dimension (7)   | cube dimension                  |
| time_slot    | dimension (4)   | cube dimension                  |
| order_type   | boolean (2)     | cube dimension                  |
| rider_id     | identifier      | excluded (120 unique riders)    |
| items        | dimension (6)   | cube dimension (coded numeric)  |
| order_value  | **measure**     | primary measure (AOV)           |
| delivery_min | **measure**     | measure (delivery time)         |
| tip          | dimension (4)   | cube dimension (coded)          |
| rating       | dimension (5)   | cube dimension (coded)          |

**Missing data:** ~4% missing rider, ~2% missing rating, ~1.5% missing order_value.

**Embedded signals:**
- Mumbai delivery times **degrade ~60% during monsoon (Jul–Aug)** — the agent
  should isolate `city=Mumbai` when asked "why did delivery times get worse."
- Beverage AOV spikes +50% in summer (Apr–Jun).
- Evening slot dominates; weekends run ~30% heavier.

**Try:**
- "Order value by city" → fast path
- "Delivery time trend" → fast path
- "Average order value by category this quarter" → fast path
- "Why did delivery times get worse?" → agent OODA loop finds Mumbai monsoon

---

## How to use

**With backend running** (real engine):
1. Start backend, set `REACT_APP_API_URL`
2. Upload either CSV via the drop zone
3. Cube builds in ~2–5s, then ask questions

**Preview files:** `*_preview.csv` (200 rows each) are for quick eyeballing only —
upload the full files to see real aggregations and the embedded signals.

**Regenerate:** `python3 _generate.py` rebuilds both from scratch (seeded, reproducible).
