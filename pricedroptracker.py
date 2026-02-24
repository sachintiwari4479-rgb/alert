import requests
import json
import sqlite3
import time
import itertools
import threading
import datetime
import os
import pandas as pd
import re
import streamlit as st

# ==========================
# CONFIGURATION
# ==========================
API_URL = "https://services.dealshare.in/feedservice/api/v1/get-page"
CATEGORY_FILE = "categories.json"
ACCOUNT_FILE = "new_accounts_detected.txt"
DB_FILE = "prices.db"

# --- TELEGRAM CONFIG ---
TELEGRAM_BOT_TOKEN = "1436736003:AAHkF0urNQ66X-Nzm0k-_L2B6gT9oNa7e5Y"
TELEGRAM_CHAT_ID = "-1001754214239"

DROP_THRESHOLD = 5
LOOP_DELAY = 600          # 10 minutes

TRACK_CATEGORY_IDS = {
    "1211", "1206", "1163", "1208", "1207", "1210", "1212",
    "1169", "1168", "1147", "1014", "1148", "1150", "1149",
    "1155", "512", "1189", "457", "499", "187", "2393",
    "2962", "1159", "2817", "641", "606"
}

# ==========================
# LOGGING (THREAD-SAFE FOR STREAMLIT)
# ==========================
@st.cache_resource
def get_log_buffer():
    # This creates a persistent list that lives across Streamlit reruns 
    # AND is accessible by background threads.
    return []

SCRAPER_LOGS = get_log_buffer()

def log(msg):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    entry = f"[{timestamp}] {msg}"
    print(entry)
    SCRAPER_LOGS.insert(0, entry)
    if len(SCRAPER_LOGS) > 100:
        SCRAPER_LOGS.pop()

def send_telegram_alert(message):
    if not TELEGRAM_BOT_TOKEN or "YOUR_BOT" in TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        log(f"❌ Telegram Error: {e}")

# ==========================
# SCRAPER LOGIC
# ==========================
def init_db():
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    
    for col in ["image_url TEXT", "offer_id TEXT", "previous_price INTEGER", 
                "real_product_id TEXT", "lowest_price INTEGER", "highest_price INTEGER", 
                "max_quantity_allowed INTEGER"]:
        try: cur.execute(f"ALTER TABLE products ADD COLUMN {col}")
        except: pass

    cur.execute("""CREATE TABLE IF NOT EXISTS products (
        product_id TEXT PRIMARY KEY, title TEXT, brand TEXT, cat_l1 TEXT, cat_l2 TEXT,
        mrp INTEGER, latest_price INTEGER, last_updated INTEGER,
        image_url TEXT, offer_id TEXT, previous_price INTEGER, 
        real_product_id TEXT, lowest_price INTEGER, highest_price INTEGER, 
        max_quantity_allowed INTEGER
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS price_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT, price INTEGER, timestamp INTEGER
    )""")
    con.commit()
    con.close()

def load_json(fpath):
    try:
        with open(fpath, "r") as f: return json.load(f)
    except: return []

def load_accounts():
    accs = []
    try:
        with open(ACCOUNT_FILE, "r") as f:
            for line in f:
                if line.strip():
                    parts = line.strip().split(":")
                    if len(parts) >= 5:
                        accs.append({"number": parts[0], "auth": parts[1], "access": parts[2], "refresh": parts[3], "devid": parts[4]})
    except: pass
    return accs

def build_headers(acc):
    return {
        "Host": "services.dealshare.in",
        "accept": "application/json, text/plain, */*",
        "accept-language": "en",
        "lang": "en",
        "appversion": "2.8.5",
        "cpversion": "1",
        "channel": "APP",
        "businessmodel": "B2C",
        "platform": "android",
        "content-type": "application/json",
        "authorization": acc["auth"],
        "authorization-access": f"{acc['access']}",
        "authorization-refresh": f"{acc['refresh']}",
        "deviceid": acc["devid"],
        "pincode": "302012",
        "user-agent": "okhttp/4.9.3",
        "cookie": f"Authorization-Refresh={acc['refresh']}; Authorization-Access={acc['access']}",
        "ab-config": '{"mov_experiment":"default","ranking_experiment":"default"}',
        "palid": "67990",
        "appsflyer-uid": "1767858753668-2894334170748306738",
        "advertisingid": "63b2e137-7932-4733-b332-38fea7c3348b",
        "instanceid": "e0d1672b228561a8fb0160b29b206f8f",
        "deliveryfeegstinfo": "true",
        "juspayenabled": "true",
        "x-datadog-origin": "rum",
        "x-datadog-sampling-priority": "1",
        "lat": "26.873780925207598",
        "lng": "75.68797392770648"
    }

def fetch_products(headers, cid):
    res = []
    off = 0
    while True:
        payload = {"pageQueryType":"PAGE","pageInfo":{"url":f"/category/l2/{cid}?screen=productListing","foldNumber":1,"version":"NEW"},"slotInfo":{"slotId":None,"componentEntityCursor":1},"lang":"en","slotPosition":0,"sortOption":{},"offset":{"adsOffset":0,"dealsOffset":off}}
        try:
            r = requests.post(API_URL, headers=headers, json=payload, timeout=15)
            if r.status_code != 200:
                log(f"⚠️ API Error {r.status_code} | {r.text[:50]}")
                break
            data = r.json()
            sections = data.get("listSection", [])
            if not sections: break
            deals = sections[0].get("contentData", {}).get("dealDetailsList", [])
            if not deals: break
            res.extend(deals)
            off += len(deals)
            if not sections[0].get("contentData", {}).get("hasNext"): break
            time.sleep(0.3)
        except Exception as e:
            log(f"Fetch Error: {e}")
            break
    return res

def save_product(d):
    now = int(time.time())
    try:
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        p_id = str(d.get("id"))
        price = int(d["price"])
        max_qty = d.get("maxQuantityAllowed")
        
        cur.execute("SELECT latest_price, previous_price, lowest_price, highest_price FROM products WHERE product_id=?", (p_id,))
        row = cur.fetchone()
        
        db_latest = row[0] if row else None
        db_prev = row[1] if row else None
        db_lowest = row[2] if row else None
        db_highest = row[3] if row else None
        
        new_lowest = db_lowest if db_lowest is not None else price
        new_highest = db_highest if db_highest is not None else price
        if price < new_lowest: new_lowest = price
        if price > new_highest: new_highest = price

        price_changed = (db_latest is None) or (price != db_latest)
        final_prev = db_prev 

        if price_changed:
            if db_latest is not None:
                final_prev = db_latest 
                
            if db_highest is not None and price < db_highest:
                discount = ((db_highest - price) / db_highest) * 100
                if db_latest is not None and price < db_latest and discount >= DROP_THRESHOLD:
                     msg = f"🚨 <b>PRICE DROP</b>\n📦 {d['title']}\n💰 <b>₹{price}</b> (High: ₹{db_highest})\n🔥 <b>{discount:.1f}% OFF</b> All-Time High"
                     send_telegram_alert(msg)
            
            cur.execute("INSERT INTO price_history (product_id, price, timestamp) VALUES (?,?,?)", (p_id, price, now))
            if db_latest is not None:
                log(f"📉 Price Change {d['title']}: ₹{db_latest} -> ₹{price}")

        cur.execute("""
        INSERT OR REPLACE INTO products 
        (product_id, title, brand, cat_l1, cat_l2, mrp, latest_price, last_updated, image_url, offer_id, previous_price, real_product_id, lowest_price, highest_price, max_quantity_allowed) 
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", 
        (p_id, d["title"], d.get("brand"), d["categoryNameL1"], d["categoryNameL2"], d["mrp"], price, now, d.get("image"), d.get("offerId"), final_prev, str(d.get("productId")), new_lowest, new_highest, max_qty))
        
        con.commit()
        con.close()
    except Exception as e: log(f"DB Error: {e}")

def scraper():
    init_db()
    if not os.path.exists(CATEGORY_FILE):
         with open(CATEGORY_FILE, "w") as f: json.dump([{"name":"Gro","subCategories":[{"name":"Masala","catId":"1211"}]}], f)
    
    cats = load_json(CATEGORY_FILE)
    accs = load_accounts()
    if not accs: 
        log("❌ No accounts detected! Check new_accounts_detected.txt")
        return
    
    cyc = itertools.cycle(accs)
    log("🚀 Background Scraper Started Successfully")
    while True:
        try:
            acc = next(cyc)
            headers = build_headers(acc)
            for p in cats:
                for c in p.get("subCategories", []):
                    cid = str(c.get("catId"))
                    if cid not in TRACK_CATEGORY_IDS: continue
                    deals = fetch_products(headers, cid)
                    if deals: 
                        log(f"✅ {c['name']}: {len(deals)} items")
                        for d in deals: save_product(d)
            
            time.sleep(LOOP_DELAY)
        except Exception as e: 
            log(f"Loop Error: {e}")
            time.sleep(60)

# Start background scraper ONLY ONCE in Streamlit
@st.cache_resource
def start_background_task():
    t = threading.Thread(target=scraper, daemon=True)
    t.start()
    return t

# ==========================
# STREAMLIT UI
# ==========================
st.set_page_config(page_title="DealSpy Pro", layout="wide", page_icon="🛍️")
start_background_task()

st.title("🛍️ DealSpy Pro")
st.markdown("Real-time DealShare Price Tracker & Analytics")

# Data Fetching
@st.cache_data(ttl=5) # Cache refreshes every 5 seconds
def load_data():
    if not os.path.exists(DB_FILE):
        return pd.DataFrame(), pd.DataFrame()
    con = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM products ORDER BY last_updated DESC", con)
    hist_df = pd.read_sql_query("SELECT * FROM price_history", con)
    con.close()
    return df, hist_df

df, history_df = load_data()

# Refresh UI Controls
col1, col2 = st.columns([1, 10])
with col1:
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()

if not df.empty:
    # --- DATA PROCESSING ---
    df['highest_price'] = df['highest_price'].fillna(df['latest_price'])
    df['lowest_price'] = df['lowest_price'].fillna(df['latest_price'])
    
    # Real Discount = (High - Current) / High
    df['real_discount'] = df.apply(lambda x: round(((x['highest_price'] - x['latest_price']) / x['highest_price']) * 100, 1) if x['highest_price'] > 0 else 0, axis=1)
    
    df['is_stable'] = df['highest_price'] == df['lowest_price']
    df['is_best_deal'] = (df['latest_price'] == df['lowest_price']) & (~df['is_stable'])
    
    # Build Deal Links safely using raw string for regex
    df['slug'] = df['title'].apply(lambda x: re.sub(r'[^a-zA-Z0-9-]', '', str(x).replace(' ', '-')))
    df['deal_link'] = "https://www.dealshare.in/pname/" + df['slug'] + "/pid/" + df['offer_id'].astype(str)

    # --- UI TABS ---
    tab1, tab2, tab3, tab4 = st.tabs(["📊 Dashboard", "📦 All Products", "📈 Price History", "📜 System Logs"])

    with tab1:
        # Key Metrics
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Items Monitored", len(df))
        m2.metric("Top Discount", f"{df['real_discount'].max()}% OFF")
        m3.metric("Real Price Drops", len(df[df['real_discount'] > 0]))
        avg_savings = df[df['real_discount'] > 0]['real_discount'].mean()
        m4.metric("Avg Savings", f"{avg_savings:.1f}%" if pd.notna(avg_savings) else "0%")

        st.markdown("### 🔥 Hottest Deals (Sorted by Best Discount)")
        top_deals = df.sort_values('real_discount', ascending=False).head(12)
        
        # Display Cards
        cols = st.columns(3)
        for idx, row in enumerate(top_deals.itertuples()):
            with cols[idx % 3]:
                badge = "💎 BEST DEAL" if row.is_best_deal else f"🔥 {row.real_discount}% OFF"
                img_url = row.image_url if pd.notna(row.image_url) else "https://via.placeholder.com/150?text=No+Image"
                
                # HTML Card
                st.markdown(f"""
                <div style="background-color: #1e293b; padding: 15px; border-radius: 10px; margin-bottom: 15px; border: 1px solid #334155;">
                    <div style="color: #10b981; font-weight: bold; font-size: 12px; margin-bottom: 5px;">{badge}</div>
                    <div style="text-align: center; margin-bottom: 10px;">
                        <img src="{img_url}" style="height: 120px; object-fit: contain;">
                    </div>
                    <div style="font-size: 14px; font-weight: 600; color: white; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="{row.title}">{row.title}</div>
                    <div style="color: #94a3b8; font-size: 11px; margin-bottom: 10px;">ID: {row.product_id}</div>
                    
                    <div style="display: flex; justify-content: space-between; align-items: end;">
                        <div>
                            <span style="text-decoration: line-through; color: #64748b; font-size: 12px;">₹{row.highest_price}</span>
                            <span style="color: white; font-size: 20px; font-weight: bold; margin-left: 5px;">₹{row.latest_price}</span>
                        </div>
                        <a href="{row.deal_link}" target="_blank" style="background-color: #4f46e5; color: white; padding: 5px 10px; border-radius: 5px; text-decoration: none; font-size: 12px; font-weight: bold;">BUY ↗</a>
                    </div>
                </div>
                """, unsafe_allow_html=True)

    with tab2:
        st.markdown("### Search & Filter All Monitored Products")
        
        # Prepare table for viewing
        display_df = df[['product_id', 'title', 'brand', 'cat_l1', 'latest_price', 'lowest_price', 'highest_price', 'real_discount', 'max_quantity_allowed', 'deal_link', 'is_best_deal']]
        
        # Allow Streamlit interactive dataframe
        st.dataframe(
            display_df,
            use_container_width=True,
            column_config={
                "deal_link": st.column_config.LinkColumn("Purchase Link", display_text="View Deal ↗"),
                "real_discount": st.column_config.NumberColumn("Discount %", format="%.1f%%"),
                "latest_price": st.column_config.NumberColumn("Current Price", format="₹%d"),
                "highest_price": st.column_config.NumberColumn("High Price", format="₹%d"),
                "lowest_price": st.column_config.NumberColumn("Low Price", format="₹%d"),
                "max_quantity_allowed": st.column_config.NumberColumn("Max Qty"),
                "is_best_deal": st.column_config.CheckboxColumn("Record Low?")
            },
            hide_index=True
        )

    with tab3:
        st.markdown("### Interactive Price History")
        if not history_df.empty:
            product_list = df['title'].unique().tolist()
            selected_product = st.selectbox("Search Product to view History Graph:", product_list)
            
            # Get ID for selected product
            sel_id = df[df['title'] == selected_product]['product_id'].iloc[0]
            
            # Filter history
            prod_hist = history_df[history_df['product_id'] == sel_id].copy()
            if not prod_hist.empty:
                prod_hist['Date & Time'] = pd.to_datetime(prod_hist['timestamp'], unit='s')
                prod_hist = prod_hist.sort_values('Date & Time')
                prod_hist.set_index('Date & Time', inplace=True)
                
                st.line_chart(prod_hist['price'], use_container_width=True)
                
                # Show tabular history
                st.markdown("#### Detailed Logs for this Product")
                st.dataframe(prod_hist[['price']].sort_index(ascending=False), use_container_width=True)
            else:
                st.info("No recorded history changes for this item yet.")

    with tab4:
        st.markdown("### Live Scraper Logs")
        log_text = "\n".join(SCRAPER_LOGS)
        st.text_area("Console Output", log_text, height=400)

else:
    st.info("⏳ Database is currently empty or loading. The scraper is running in the background. Please wait a moment and refresh.")
    if SCRAPER_LOGS:
        st.markdown("### Live Logs:")
        for l in SCRAPER_LOGS:
            st.code(l)
