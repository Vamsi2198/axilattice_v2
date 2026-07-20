"""
Realistic dataset generator for Axilattice.
Produces two CSVs with:
  - proper temporal column (daily grain over ~1 year)
  - multiple low-cardinality dimensions (cube-worthy)
  - at least one high-cardinality dimension (should be EXCLUDED by profiler)
  - an identifier column (should be excluded)
  - multiple numeric measures with realistic distributions
  - intentional missing data (nulls) to mimic real pipelines
  - embedded signals (a drop, a seasonal spike) for the agent to discover
"""

import csv
import random
import datetime as dt
from pathlib import Path

random.seed(42)
OUT = Path("/mnt/user-data/outputs/axilattice/sample_data")
OUT.mkdir(parents=True, exist_ok=True)

# ══════════════════════════════════════════════════════════════════════════
# 1. E-COMMERCE  (order-level, daily over 2024)
# ══════════════════════════════════════════════════════════════════════════
def gen_ecommerce():
    regions    = ["North", "South", "East", "West", "Central"]
    categories = ["Electronics", "Apparel", "Home", "Beauty", "Sports", "Books"]
    channels   = ["Web", "iOS App", "Android App", "Marketplace"]
    segments   = ["New", "Returning", "VIP"]
    payment    = ["Card", "UPI", "COD", "Wallet"]
    # high-cardinality dim → should be EXCLUDED from cube
    cities     = [f"City_{i:03d}" for i in range(80)]

    region_base = {"North": 1.2, "South": 1.0, "East": 1.1, "West": 0.9, "Central": 0.8}
    cat_price   = {"Electronics": 8500, "Apparel": 1800, "Home": 3200,
                   "Beauty": 950, "Sports": 2400, "Books": 550}
    cat_margin  = {"Electronics": 0.12, "Apparel": 0.35, "Home": 0.28,
                   "Beauty": 0.42, "Sports": 0.30, "Books": 0.22}

    start = dt.date(2024, 1, 1)
    rows = []
    oid = 100000

    for day_offset in range(366):  # full leap year 2024
        date = start + dt.timedelta(days=day_offset)
        month = date.month
        dow = date.weekday()  # 0=Mon

        # seasonality: festive spike Oct-Nov, weekend lift
        season = 1.0
        if month in (10, 11):          season = 1.45   # festive
        elif month == 12:              season = 1.25   # holiday
        elif month in (6, 7):          season = 0.85   # summer lull
        weekend = 1.20 if dow >= 5 else 1.0

        # embedded SIGNAL: Electronics supply crunch in September → drop
        base_orders = int(random.gauss(140, 25) * season * weekend)
        base_orders = max(40, base_orders)

        for _ in range(base_orders):
            region  = random.choices(list(region_base), weights=list(region_base.values()))[0]
            category= random.choice(categories)
            channel = random.choices(channels, weights=[0.35, 0.25, 0.30, 0.10])[0]
            segment = random.choices(segments, weights=[0.45, 0.42, 0.13])[0]
            pay     = random.choices(payment, weights=[0.40, 0.35, 0.15, 0.10])[0]
            city    = random.choice(cities)

            # SIGNAL: electronics revenue craters in September
            supply_hit = 0.55 if (category == "Electronics" and month == 9) else 1.0

            qty   = random.choices([1, 2, 3, 4], weights=[0.6, 0.25, 0.1, 0.05])[0]
            price = cat_price[category] * random.uniform(0.7, 1.4)
            revenue = round(price * qty * region_base[region] * supply_hit, 2)
            margin  = round(cat_margin[category] * random.uniform(0.8, 1.2), 4)
            discount= round(random.choices([0, 5, 10, 15, 20],
                            weights=[0.4, 0.2, 0.2, 0.1, 0.1])[0] / 100 * revenue, 2)
            ship_days = random.choices([1, 2, 3, 4, 5, 7], weights=[0.2, 0.3, 0.25, 0.1, 0.1, 0.05])[0]

            oid += 1
            # intentional missing data
            if random.random() < 0.03:  city = ""          # 3% missing city
            if random.random() < 0.02:  segment = ""        # 2% missing segment
            rev_out = "" if random.random() < 0.01 else revenue  # 1% missing revenue

            rows.append({
                "order_id":    f"ORD-{oid}",
                "order_date":  date.isoformat(),
                "region":      region,
                "category":    category,
                "channel":     channel,
                "segment":     segment,
                "payment":     pay,
                "city":        city,
                "quantity":    qty,
                "revenue":     rev_out,
                "margin_pct":  margin,
                "discount":    discount,
                "ship_days":   ship_days,
            })

    _write(OUT / "ecommerce_orders.csv", rows)
    return len(rows)


# ══════════════════════════════════════════════════════════════════════════
# 2. QUICK-COMMERCE  (delivery-level, daily; hyperlocal, sub-30-min)
# ══════════════════════════════════════════════════════════════════════════
def gen_quickcommerce():
    cities      = ["Bengaluru", "Mumbai", "Delhi", "Hyderabad", "Pune", "Chennai"]
    dark_stores = ["DS_HSR", "DS_Koramangala", "DS_Andheri", "DS_Bandra",
                   "DS_Saket", "DS_Gachibowli", "DS_Kothrud", "DS_Adyar"]  # low-card
    categories  = ["Grocery", "Fresh", "Snacks", "Beverages", "Personal Care",
                   "Household", "Pharmacy"]
    slots       = ["Morning", "Afternoon", "Evening", "Night"]
    order_type  = ["Scheduled", "Instant"]
    # high-cardinality → excluded
    rider_ids   = [f"RIDER-{i:04d}" for i in range(120)]

    cat_aov = {"Grocery": 480, "Fresh": 320, "Snacks": 180, "Beverages": 220,
               "Personal Care": 350, "Household": 410, "Pharmacy": 260}

    start = dt.date(2024, 1, 1)
    rows = []
    did = 500000

    for day_offset in range(366):
        date = start + dt.timedelta(days=day_offset)
        month = date.month
        dow = date.weekday()

        # quick-commerce: evening + weekend heavy; summer beverage spike
        weekend = 1.30 if dow >= 5 else 1.0
        summer_bev = 1.5 if month in (4, 5, 6) else 1.0

        base_deliveries = int(random.gauss(200, 35) * weekend)
        base_deliveries = max(60, base_deliveries)

        for _ in range(base_deliveries):
            city   = random.choice(cities)
            store  = random.choice(dark_stores)
            category = random.choice(categories)
            slot   = random.choices(slots, weights=[0.2, 0.2, 0.4, 0.2])[0]
            otype  = random.choices(order_type, weights=[0.15, 0.85])[0]
            rider  = random.choice(rider_ids)

            bev_boost = summer_bev if category == "Beverages" else 1.0
            # SIGNAL: delivery times degrade in Mumbai during monsoon (Jul-Aug)
            monsoon_delay = 1.6 if (city == "Mumbai" and month in (7, 8)) else 1.0

            items  = random.choices([2, 3, 4, 5, 6, 8], weights=[0.15, 0.25, 0.25, 0.15, 0.15, 0.05])[0]
            aov    = round(cat_aov[category] * random.uniform(0.6, 1.6) * bev_boost, 2)
            deliver_min = round(random.gauss(14, 4) * monsoon_delay, 1)
            deliver_min = max(6.0, deliver_min)
            tip    = round(random.choices([0, 10, 20, 30], weights=[0.6, 0.2, 0.15, 0.05])[0], 2)
            rating = random.choices([5, 4, 3, 2, 1], weights=[0.55, 0.25, 0.12, 0.05, 0.03])[0]

            did += 1
            # missing data
            if random.random() < 0.04:  rider = ""            # 4% missing rider
            if random.random() < 0.02:  rating = ""           # 2% missing rating
            aov_out = "" if random.random() < 0.015 else aov  # 1.5% missing aov

            rows.append({
                "delivery_id":    f"DEL-{did}",
                "order_date":     date.isoformat(),
                "city":           city,
                "dark_store":     store,
                "category":       category,
                "time_slot":      slot,
                "order_type":     otype,
                "rider_id":       rider,
                "items":          items,
                "order_value":    aov_out,
                "delivery_min":   deliver_min,
                "tip":            tip,
                "rating":         rating,
            })

    _write(OUT / "quickcommerce_deliveries.csv", rows)
    return len(rows)


def _write(path, rows):
    if not rows:
        return
    fields = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    n1 = gen_ecommerce()
    n2 = gen_quickcommerce()
    print(f"ecommerce_orders.csv         : {n1:,} rows")
    print(f"quickcommerce_deliveries.csv : {n2:,} rows")
