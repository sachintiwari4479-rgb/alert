import requests
import json
import sqlite3
import time
import itertools
import threading
import datetime
import os
from flask import Flask, jsonify, render_template_string

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
LOOP_DELAY = 600  # 10 minutes

TRACK_CATEGORY_IDS = {
    "1211", "1206", "1163", "1208", "1207", "1210", "1212",
    "1169", "1168", "1147", "1014", "1148", "1150", "1149",
    "1155", "512", "1189", "457", "499", "187", "2393",
    "2962", "1159", "2817", "641", "606"
}

SCRAPER_LOGS = []


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
# FLASK APP
# ==========================
app = Flask(__name__)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DealSpy Pro</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet">
    <style> 
        body { font-family: 'Inter', sans-serif; } 
        .no-scrollbar::-webkit-scrollbar { display: none; }
        .no-scrollbar { -ms-overflow-style: none;  scrollbar-width: none; }
        [x-cloak] { display: none !important; }
    </style>
</head>
<body class="bg-slate-900 text-slate-100" x-data="dashboard()">

    <!-- Sidebar -->
    <div class="hidden md:flex fixed inset-y-0 left-0 w-64 bg-slate-950 border-r border-slate-800 p-6 flex-col z-50">
        <div class="flex items-center gap-3 mb-8">
            <div class="bg-indigo-600 p-2 rounded-lg text-white">
                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
            </div>
            <h1 class="font-bold text-xl text-white">DealSpy Pro</h1>
        </div>

        <div class="space-y-2">
            <button @click="tab = 'dashboard'" :class="tab === 'dashboard' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:bg-slate-800'" class="w-full flex items-center gap-3 px-4 py-3 rounded-xl font-medium transition-colors">
                <span>📊</span> Dashboard
            </button>
            <button @click="tab = 'products'" :class="tab === 'products' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:bg-slate-800'" class="w-full flex items-center gap-3 px-4 py-3 rounded-xl font-medium transition-colors">
                <span>📦</span> All Products
            </button>
            <button @click="tab = 'logs'" :class="tab === 'logs' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:bg-slate-800'" class="w-full flex items-center gap-3 px-4 py-3 rounded-xl font-medium transition-colors">
                <span>📜</span> System Logs
            </button>
        </div>

        <div class="mt-auto bg-slate-800 rounded-xl p-4 text-slate-300 border border-slate-700">
            <div class="flex items-center gap-2 text-sm font-medium mb-1">
                <span :class="loading ? 'bg-yellow-400' : 'bg-green-400'" class="w-2 h-2 rounded-full animate-pulse"></span>
                <span x-text="loading ? 'Scanning...' : 'System Active'"></span>
            </div>
            <p class="text-xs text-slate-500" x-text="'Last: ' + lastRefreshed"></p>
        </div>
    </div>

    <!-- Mobile Nav -->
    <div class="md:hidden fixed bottom-0 left-0 right-0 bg-slate-950 border-t border-slate-800 p-2 flex justify-around z-50 pb-safe">
        <button @click="tab = 'dashboard'" :class="tab === 'dashboard' ? 'text-indigo-500' : 'text-slate-500'" class="flex flex-col items-center p-2">
            <span class="text-[10px] font-medium mt-1">Dash</span>
        </button>
        <button @click="tab = 'products'" :class="tab === 'products' ? 'text-indigo-500' : 'text-slate-500'" class="flex flex-col items-center p-2">
            <span class="text-[10px] font-medium mt-1">Items</span>
        </button>
         <button @click="tab = 'logs'" :class="tab === 'logs' ? 'text-indigo-500' : 'text-slate-500'" class="flex flex-col items-center p-2">
            <span class="text-[10px] font-medium mt-1">Logs</span>
        </button>
    </div>

    <!-- Main Content -->
    <div class="md:ml-64 p-4 md:p-8 min-h-screen pb-20 md:pb-8 bg-slate-900">

        <!-- History Modal -->
        <div x-show="showHistoryModal" x-cloak class="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
            <div class="bg-slate-900 w-full max-w-2xl rounded-2xl border border-slate-700 shadow-2xl flex flex-col max-h-[90vh]" @click.away="showHistoryModal = false">
                <div class="p-4 border-b border-slate-800 flex justify-between items-center">
                    <h3 class="font-bold text-lg text-white">Price History</h3>
                    <button @click="showHistoryModal = false" class="text-slate-400 hover:text-white">✕</button>
                </div>

                <div class="p-6 overflow-y-auto">
                    <div class="h-64 w-full mb-6 bg-slate-800/50 rounded-xl p-2">
                         <canvas id="historyChart"></canvas>
                    </div>
                    <h4 class="text-sm font-bold text-slate-400 uppercase mb-2">Detailed Log</h4>
                    <div class="bg-slate-800 rounded-xl overflow-hidden">
                        <table class="w-full text-left text-sm">
                            <thead class="bg-slate-950 text-slate-400 text-xs uppercase">
                                <tr>
                                    <th class="px-4 py-3">Time</th>
                                    <th class="px-4 py-3">Price</th>
                                </tr>
                            </thead>
                            <tbody class="divide-y divide-slate-700">
                                <template x-for="(h, index) in historyData" :key="index">
                                    <tr class="hover:bg-slate-700/50">
                                        <td class="px-4 py-3 text-slate-300" x-text="h.time"></td>
                                        <td class="px-4 py-3 font-bold text-white" x-text="'₹' + h.price"></td>
                                    </tr>
                                </template>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <!-- Header -->
        <div class="flex justify-between items-center mb-6 md:mb-8">
             <h2 class="text-xl md:text-2xl font-bold text-white tracking-tight" x-text="tab.charAt(0).toUpperCase() + tab.slice(1)"></h2>
            <div class="flex gap-3">
                <button @click="fetchData()" class="bg-indigo-600 p-2 px-4 rounded-lg text-white hover:bg-indigo-500 transition-all text-sm font-medium shadow-lg shadow-indigo-600/20">
                    Refresh
                </button>
            </div>
        </div>

        <!-- LOGS -->
        <div x-show="tab === 'logs'" class="bg-black text-green-400 p-4 rounded-xl font-mono text-xs md:text-sm h-[75vh] overflow-y-auto shadow-2xl border border-slate-800">
            <template x-for="log in logs">
                <div class="mb-1.5 border-b border-slate-900 pb-1 last:border-0 break-words" x-text="log"></div>
            </template>
        </div>

        <!-- DASHBOARD -->
        <div x-show="tab === 'dashboard'" class="space-y-6">
            <!-- Stats -->
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div class="bg-slate-800 p-4 rounded-xl border border-slate-700 shadow-sm">
                    <h4 class="text-slate-400 text-xs font-medium uppercase tracking-wider">Monitored</h4>
                    <p class="text-2xl font-bold mt-1 text-white" x-text="stats.total"></p>
                </div>
                <div class="bg-slate-800 p-4 rounded-xl border border-slate-700 shadow-sm">
                    <h4 class="text-slate-400 text-xs font-medium uppercase tracking-wider">Top Deal</h4>
                    <p class="text-2xl font-bold mt-1 text-emerald-400" x-text="stats.topDiscount + '% OFF'"></p>
                </div>
                 <div class="bg-slate-800 p-4 rounded-xl border border-slate-700 shadow-sm">
                    <h4 class="text-slate-400 text-xs font-medium uppercase tracking-wider">Real Drops</h4>
                    <p class="text-2xl font-bold mt-1 text-yellow-400" x-text="stats.realDrops"></p>
                </div>
                <div class="bg-slate-800 p-4 rounded-xl border border-slate-700 shadow-sm">
                    <h4 class="text-slate-400 text-xs font-medium uppercase tracking-wider">Avg Savings</h4>
                    <p class="text-2xl font-bold mt-1 text-indigo-400" x-text="stats.avgSavings + '%'"></p>
                </div>
            </div>

            <!-- Cards -->
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <template x-for="p in topDrops" :key="p.product_id">
                    <div class="flex flex-col bg-slate-900 border border-slate-700 rounded-xl p-4 hover:border-indigo-500 transition-all group relative">

                        <!-- Smart Badges -->
                        <div x-show="p.is_best_deal" class="absolute top-0 right-0 bg-gradient-to-r from-yellow-500 to-orange-500 text-black text-[10px] font-bold px-2 py-1 rounded-bl-lg z-10 shadow-lg">
                            💎 BEST DEAL
                        </div>
                        <div x-show="!p.is_best_deal && p.real_discount > 0" class="absolute top-0 right-0 bg-emerald-600 text-white text-[10px] font-bold px-2 py-1 rounded-bl-lg z-10">
                            <span x-text="'🔥 ' + p.real_discount + '% OFF'"></span>
                        </div>

                        <!-- Clickable Image -->
                        <a :href="getDealShareLink(p)" target="_blank" class="w-full h-32 mb-3 bg-white rounded-lg flex items-center justify-center p-2 relative cursor-pointer group-hover:scale-[1.02] transition-transform">
                            <img x-show="p.image_url" :src="p.image_url" class="h-full w-full object-contain" alt="img">
                        </a>

                        <!-- Clickable Title -->
                        <div class="flex justify-between items-start gap-2">
                            <a :href="getDealShareLink(p)" target="_blank" class="font-semibold text-slate-200 text-sm truncate hover:text-indigo-400 transition-colors flex-1" x-text="p.title"></a>
                        </div>

                        <!-- Smart Price Grid -->
                        <div class="mt-4 grid grid-cols-3 gap-2 text-center text-[10px] text-slate-400 bg-slate-950/50 rounded-lg p-2 border border-slate-800">
                            <div>
                                <div class="uppercase tracking-wide opacity-70">High</div>
                                <div class="text-red-400 font-medium" x-text="'₹' + p.highest_price"></div>
                            </div>
                            <div>
                                <div class="uppercase tracking-wide opacity-70">Low</div>
                                <div class="text-emerald-400 font-medium" x-text="'₹' + p.lowest_price"></div>
                            </div>
                            <div>
                                <div class="uppercase tracking-wide opacity-70">Now</div>
                                <div class="text-white font-bold" x-text="'₹' + p.latest_price"></div>
                            </div>
                        </div>

                        <div class="mt-3 flex justify-between items-center">
                            <div class="text-xs text-slate-500">
                                <span x-show="p.is_stable">Price Stable</span>
                                <span x-show="!p.is_stable && p.real_discount > 0" class="text-emerald-400 font-medium">Great Price!</span>
                            </div>
                            <div class="flex gap-2">
                                <button @click.stop="openHistory(p.product_id)" class="text-slate-400 hover:text-white text-xs underline cursor-pointer">History</button>
                                <a :href="getDealShareLink(p)" target="_blank" class="text-indigo-400 hover:text-white text-xs font-bold border border-indigo-500/50 px-2 py-1 rounded bg-indigo-500/10">BUY ↗</a>
                            </div>
                        </div>
                    </div>
                </template>
            </div>
        </div>

        <!-- LIST VIEW -->
        <div x-show="tab === 'products'" class="bg-slate-800 rounded-xl border border-slate-700 shadow-sm overflow-hidden">
             <div class="p-4 border-b border-slate-700 flex flex-col md:flex-row gap-4">
                <input type="text" x-model="search" placeholder="Search products..." class="w-full md:w-64 bg-slate-900 border border-slate-600 rounded-lg px-4 py-2 text-sm text-white focus:outline-none focus:border-indigo-500">

                <!-- Filters -->
                <div class="flex gap-2 flex-1 overflow-x-auto no-scrollbar">
                    <select x-model="sortOption" class="bg-slate-900 border border-slate-600 rounded-lg px-4 py-2 text-sm text-white focus:outline-none focus:border-indigo-500">
                        <option value="discount">🔥 Top Discount</option>
                        <option value="price_high">💰 Price: High to Low</option>
                        <option value="price_low">💰 Price: Low to High</option>
                    </select>

                    <select x-model="brandFilter" class="bg-slate-900 border border-slate-600 rounded-lg px-4 py-2 text-sm text-white focus:outline-none focus:border-indigo-500 max-w-[150px]">
                        <option value="All">All Brands</option>
                        <template x-for="b in brands" :key="b">
                            <option :value="b" x-text="b"></option>
                        </template>
                    </select>
                </div>
            </div>
            <div class="overflow-x-auto no-scrollbar">
                <table class="w-full text-left min-w-[600px]">
                    <thead class="bg-slate-900 text-xs text-slate-400 uppercase font-bold tracking-wider">
                        <tr>
                            <th class="px-6 py-4">Status</th>
                            <th class="px-6 py-4">Product</th>
                            <th class="px-6 py-4">Current</th>
                            <th class="px-6 py-4">High/Low</th>
                            <th class="px-6 py-4">Discount</th>
                            <th class="px-6 py-4">Actions</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-slate-700 text-sm">
                        <template x-for="p in filteredProducts" :key="p.product_id">
                            <tr class="hover:bg-slate-700/50 transition-colors">
                                <td class="px-6 py-4">
                                    <span x-show="p.is_best_deal" class="text-[10px] font-bold bg-yellow-500/20 text-yellow-400 px-2 py-1 rounded border border-yellow-500/30">BEST</span>
                                    <span x-show="!p.is_best_deal && p.is_stable" class="text-[10px] text-slate-500">STABLE</span>
                                    <span x-show="!p.is_best_deal && !p.is_stable" class="text-[10px] font-bold text-slate-300">ACTIVE</span>
                                </td>
                                <td class="px-6 py-4">
                                    <div class="font-medium text-slate-200" x-text="p.title"></div>
                                    <div class="flex gap-2 text-[10px] text-slate-500 font-mono items-center mt-1">
                                        <span x-text="p.product_id"></span>
                                        <span x-show="p.brand" class="bg-slate-700 px-1 rounded text-slate-300" x-text="p.brand"></span>
                                        <span x-show="p.max_quantity_allowed" x-text="'• Max Qty: ' + p.max_quantity_allowed" class="text-indigo-400"></span>
                                    </div>
                                </td>
                                <td class="px-6 py-4">
                                    <div class="font-bold text-white" x-text="'₹' + p.latest_price"></div>
                                </td>
                                <td class="px-6 py-4">
                                    <div class="text-xs text-red-400" x-text="'High: ₹' + p.highest_price"></div>
                                    <div class="text-xs text-emerald-400" x-text="'Low: ₹' + p.lowest_price"></div>
                                </td>
                                <td class="px-6 py-4">
                                    <div x-show="p.real_discount > 0" class="text-emerald-400 font-bold" x-text="p.real_discount + '%'"></div>
                                    <div x-show="p.real_discount <= 0" class="text-slate-600">-</div>
                                </td>
                                <td class="px-6 py-4 flex gap-3 items-center">
                                    <a :href="getDealShareLink(p)" target="_blank" class="text-indigo-400 hover:text-white text-xs font-bold">View</a>
                                    <button @click="openHistory(p.product_id)" class="text-slate-400 hover:text-white text-xs underline cursor-pointer">Hist</button>
                                </td>
                            </tr>
                        </template>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        let chartInstance = null;

        function dashboard() {
            return {
                tab: 'dashboard',
                products: [],
                logs: [],
                search: '',
                sortOption: 'discount', 
                brandFilter: 'All',
                lastRefreshed: '-',
                loading: false,
                showHistoryModal: false,
                historyData: [],

                get brands() {
                    return [...new Set(this.products.map(p => p.brand).filter(Boolean))].sort();
                },

                get stats() {
                    const total = this.products.length;
                    const realDrops = this.products.filter(p => p.real_discount > 0).length;
                    const topDiscount = this.products.length > 0 ? Math.max(...this.products.map(p => p.real_discount)) : 0;

                    const savings = this.products.filter(p => p.real_discount > 0).map(p => p.real_discount);
                    const avgSavings = savings.length > 0 ? savings.reduce((a, b) => a + b, 0) / savings.length : 0;

                    return { total, realDrops, topDiscount: topDiscount.toFixed(0), avgSavings: avgSavings.toFixed(1) };
                },
                get topDrops() { 
                    return [...this.products]
                        .sort((a, b) => b.real_discount - a.real_discount)
                        .slice(0, 15); 
                },
                get filteredProducts() { 
                    let filtered = this.products.filter(p => {
                        const matchesSearch = p.title.toLowerCase().includes(this.search.toLowerCase());
                        const matchesBrand = this.brandFilter === 'All' || p.brand === this.brandFilter;
                        return matchesSearch && matchesBrand;
                    });

                    if (this.sortOption === 'discount') {
                        return filtered.sort((a, b) => b.real_discount - a.real_discount);
                    } else if (this.sortOption === 'price_high') {
                        return filtered.sort((a, b) => b.latest_price - a.latest_price);
                    } else if (this.sortOption === 'price_low') {
                        return filtered.sort((a, b) => a.latest_price - b.latest_price);
                    }
                    return filtered;
                },
                getDealShareLink(product) {
                    if (!product.offer_id) return '#';
                    const slug = product.title.replace(/\s+/g, '-').replace(/[^a-zA-Z0-9-]/g, '');
                    return `https://www.dealshare.in/pname/${slug}/pid/${product.offer_id}`;
                },

                async openHistory(pid) {
                    this.showHistoryModal = true;
                    this.historyData = []; 

                    try {
                        const res = await fetch(`/api/history/${pid}`);
                        const data = await res.json();
                        this.historyData = data;
                        this.renderChart(data);
                    } catch(e) { console.error(e); }
                },

                renderChart(data) {
                    const ctx = document.getElementById('historyChart').getContext('2d');
                    if (chartInstance) chartInstance.destroy();

                    const chartData = [...data].reverse(); 

                    chartInstance = new Chart(ctx, {
                        type: 'line',
                        data: {
                            labels: chartData.map(d => d.time),
                            datasets: [{
                                label: 'Price (₹)',
                                data: chartData.map(d => d.price),
                                borderColor: '#10b981',
                                backgroundColor: 'rgba(16, 185, 129, 0.1)',
                                borderWidth: 2,
                                tension: 0.1,
                                fill: true
                            }]
                        },
                        options: {
                            responsive: true,
                            maintainAspectRatio: false,
                            plugins: { legend: { display: false } },
                            scales: {
                                y: { grid: { color: '#1e293b' }, ticks: { color: '#94a3b8' } },
                                x: { grid: { display: false }, ticks: { display: false } }
                            }
                        }
                    });
                },

                init() {
                    this.fetchData();
                    setInterval(() => this.fetchData(), 5000); 
                },
                async fetchData() {
                    this.loading = true;
                    try {
                        const res = await fetch('/api/data?t=' + new Date().getTime());
                        if (res.ok) {
                            const data = await res.json();
                            this.products = data.products;
                            this.logs = data.logs;
                            this.lastRefreshed = new Date().toLocaleTimeString();
                        }
                    } catch (e) { console.error("Error", e); } 
                    finally { this.loading = false; }
                }
            }
        }
    </script>
</body>
</html>
"""


@app.route('/')
def index(): return render_template_string(DASHBOARD_HTML)


@app.route('/api/data')
def get_data():
    products = []
    try:
        con = sqlite3.connect(DB_FILE)
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        cur.execute("SELECT * FROM products ORDER BY last_updated DESC")
        rows = cur.fetchall()

        for row in rows:
            p = dict(row)

            curr = p['latest_price']
            high = p.get('highest_price') if p.get('highest_price') else curr
            low = p.get('lowest_price') if p.get('lowest_price') else curr

            p['highest_price'] = high
            p['lowest_price'] = low

            if high > 0:
                p['real_discount'] = round(((high - curr) / high) * 100, 1)
            else:
                p['real_discount'] = 0.0

            p['is_stable'] = (high == low)
            p['is_best_deal'] = (curr == low) and (not p['is_stable'])

            products.append(p)
        con.close()
    except Exception as e:
        log(f"API Error: {e}")

    return jsonify({"products": products, "logs": SCRAPER_LOGS})


@app.route('/api/history/<pid>')
def get_history(pid):
    data = []
    try:
        con = sqlite3.connect(DB_FILE)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("SELECT price, timestamp FROM price_history WHERE product_id=? ORDER BY timestamp DESC LIMIT 50",
                    (pid,))
        rows = cur.fetchall()
        for r in rows:
            dt = datetime.datetime.fromtimestamp(r['timestamp']).strftime("%d %b %H:%M")
            data.append({"price": r['price'], "time": dt})
        con.close()
    except Exception as e:
        log(f"History Error: {e}")
    return jsonify(data)


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
        try:
            cur.execute(f"ALTER TABLE products ADD COLUMN {col}")
        except:
            pass

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
        with open(fpath, "r") as f:
            return json.load(f)
    except:
        return []


def load_accounts():
    accs = []
    try:
        with open(ACCOUNT_FILE, "r") as f:
            for line in f:
                if line.strip():
                    parts = line.strip().split(":")
                    if len(parts) >= 5:
                        accs.append({"number": parts[0], "auth": parts[1], "access": parts[2], "refresh": parts[3],
                                     "devid": parts[4]})
    except:
        pass
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
        payload = {"pageQueryType": "PAGE",
                   "pageInfo": {"url": f"/category/l2/{cid}?screen=productListing", "foldNumber": 1, "version": "NEW"},
                   "slotInfo": {"slotId": None, "componentEntityCursor": 1}, "lang": "en", "slotPosition": 0,
                   "sortOption": {}, "offset": {"adsOffset": 0, "dealsOffset": off}}
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

        cur.execute("SELECT latest_price, previous_price, lowest_price, highest_price FROM products WHERE product_id=?",
                    (p_id,))
        row = cur.fetchone()

        db_latest = row[0] if row else None
        db_prev = row[1] if row else None
        db_lowest = row[2] if row else None
        db_highest = row[3] if row else None

        # Determine High/Low
        new_lowest = db_lowest if db_lowest is not None else price
        new_highest = db_highest if db_highest is not None else price
        if price < new_lowest: new_lowest = price
        if price > new_highest: new_highest = price

        # DETERMINE IF PRICE CHANGED
        price_changed = (db_latest is None) or (price != db_latest)
        final_prev = db_prev  # Default keep existing previous

        if price_changed:
            if db_latest is not None:
                final_prev = db_latest  # Only update prev if there was a history

            # Price Drop Alerts logic
            if db_highest is not None and price < db_highest:
                discount = ((db_highest - price) / db_highest) * 100
                if db_latest is not None and price < db_latest and discount >= DROP_THRESHOLD:
                    msg = f"🚨 <b>PRICE DROP</b>\n📦 {d['title']}\n💰 <b>₹{price}</b> (High: ₹{db_highest})\n🔥 <b>{discount:.1f}% OFF</b> All-Time High"
                    send_telegram_alert(msg)

            # INSERT HISTORY ONLY ON CHANGE
            cur.execute("INSERT INTO price_history (product_id, price, timestamp) VALUES (?,?,?)", (p_id, price, now))
            log(f"📉 Price Change {d['title']}: ₹{db_latest} -> ₹{price}")

        # ALWAYS UPDATE PRODUCT TABLE
        cur.execute("""
        INSERT OR REPLACE INTO products 
        (product_id, title, brand, cat_l1, cat_l2, mrp, latest_price, last_updated, image_url, offer_id, previous_price, real_product_id, lowest_price, highest_price, max_quantity_allowed) 
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (p_id, d["title"], d.get("brand"), d["categoryNameL1"], d["categoryNameL2"], d["mrp"], price, now,
                     d.get("image"), d.get("offerId"), final_prev, str(d.get("productId")), new_lowest, new_highest,
                     max_qty))

        con.commit()
        con.close()
    except Exception as e:
        log(f"DB Error: {e}")


def scraper():
    init_db()
    if not os.path.exists(CATEGORY_FILE):
        with open(CATEGORY_FILE, "w") as f: json.dump(
            [{"name": "Gro", "subCategories": [{"name": "Masala", "catId": "1211"}]}], f)

    cats = load_json(CATEGORY_FILE)
    accs = load_accounts()
    if not accs: return

    cyc = itertools.cycle(accs)
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


if __name__ == "__main__":
    threading.Thread(target=scraper, daemon=True).start()
    app.run(port=5000, debug=True, use_reloader=False)