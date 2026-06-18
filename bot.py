from flask import Flask, jsonify, request, Response, session, redirect, url_for
import requests
import os
from datetime import datetime, timedelta
from functools import wraps
import anthropic
import pg8000.native
from mindmap import get_mindmap_html
from wandaebon import get_wandaebon_html, parse_wandaebon_docx

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # 32MB
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)  # 30일 유지
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = True  # HTTPS only
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.secret_key = os.environ.get("SECRET_KEY", "yenny-dashboard-secret-2026-change-me")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
KIS_APP_KEY = os.environ.get("KIS_APP_KEY", "")
KIS_APP_SECRET = os.environ.get("KIS_APP_SECRET", "")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "yenny2026")
API_SECRET = os.environ.get("API_SECRET", "moneyplus")

_kis_token_cache = {"token": "", "expires": 0}


def get_db():
    import urllib.parse as _up
    r = _up.urlparse(DATABASE_URL)
    return pg8000.native.Connection(
        host=r.hostname, port=r.port or 5432,
        database=r.path.lstrip('/'),
        user=r.username, password=r.password,
        ssl_context=True
    )

def init_db():
    conn = get_db()
    conn.run("""CREATE TABLE IF NOT EXISTS posts (
        id SERIAL PRIMARY KEY,
        type TEXT NOT NULL, content TEXT NOT NULL,
        date TEXT NOT NULL, created_at TEXT NOT NULL)""")
    try:
        conn.run("""CREATE TABLE IF NOT EXISTS pdfs (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL)""")
    except Exception:
        pass
    conn.close()

init_db()


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # 1. 세션 기반 로그인 체크 (PWA/폼 로그인)
        if session.get('logged_in'):
            return f(*args, **kwargs)
        # 2. X-API-Secret 헤더 체크 (봇/PWA fallback)
        api_secret_header = request.headers.get("X-API-Secret", "")
        if api_secret_header and api_secret_header == API_SECRET:
            return f(*args, **kwargs)
        # 3. Basic Auth 체크 (봇/API 호출용)
        auth = request.authorization
        if auth and auth.password == DASHBOARD_PASSWORD:
            return f(*args, **kwargs)
        # 4. JSON 요청이면 401, 일반 페이지면 로그인 페이지로
        if request.path.startswith('/api/'):
            return jsonify({"error": "unauthorized"}), 401
        return redirect(url_for('login', next=request.path))
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == DASHBOARD_PASSWORD:
            session.permanent = True
            session['logged_in'] = True
            next_url = request.args.get('next', '/')
            # 안전한 redirect만 허용
            if not next_url.startswith('/'):
                next_url = '/'
            return redirect(next_url)
        error = "비밀번호가 틀렸어요"
    else:
        error = None
    
    err_html = f'<div style="color:#d63031;font-size:13px;margin-top:10px;text-align:center;">{error}</div>' if error else ''
    return Response("""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Yenny">
<link rel="manifest" href="/manifest.json">
<title>Yenny Dashboard · 로그인</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,'Noto Sans KR',sans-serif;background:linear-gradient(135deg,#1a1d23,#2d3436);min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.card{background:white;border-radius:20px;padding:40px 32px;width:100%;max-width:380px;box-shadow:0 20px 60px rgba(0,0,0,.3)}
.logo{font-size:48px;text-align:center;margin-bottom:16px}
h1{font-size:24px;text-align:center;color:#1a1d23;margin-bottom:8px;font-weight:700}
.sub{font-size:13px;color:#7a8099;text-align:center;margin-bottom:28px}
input{width:100%;padding:14px 16px;border:1.5px solid #dfe6e9;border-radius:12px;font-size:15px;font-family:inherit;background:#f8f9fa;outline:none;transition:all .15s}
input:focus{border-color:#e8b84b;background:white}
button{width:100%;padding:14px;margin-top:12px;background:#1a1d23;color:#e8b84b;border:none;border-radius:12px;font-size:15px;font-weight:700;cursor:pointer;font-family:inherit;transition:all .15s}
button:hover{background:#2d3436}
</style>
</head>
<body>
<div class="card">
<div class="logo">🎙️</div>
<h1>Yenny Dashboard</h1>
<p class="sub">방송 준비를 시작할까요?</p>
<form method="POST" action="/login""" + (f"?next={request.args.get('next','/')}" if request.args.get('next') else "") + """">
<input type="password" name="password" placeholder="비밀번호" autofocus required autocomplete="current-password">
<button type="submit">로그인</button>
""" + err_html + """
</form>
</div>
</body>
</html>""", mimetype="text/html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login'))
def get_kis_token():
    import time
    if _kis_token_cache["token"] and time.time() < _kis_token_cache["expires"]:
        return _kis_token_cache["token"]
    try:
        r = requests.post(
            "https://openapi.koreainvestment.com:9443/oauth2/tokenP",
            json={"grant_type": "client_credentials",
                  "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET},
            timeout=6)
        data = r.json()
        token = data.get("access_token", "")
        _kis_token_cache["token"] = token
        _kis_token_cache["expires"] = time.time() + 3600 * 20  # 20시간
        return token
    except:
        return ""


def kis_get(path, tr_id, params):
    if not KIS_APP_KEY:
        return {}
    try:
        token = get_kis_token()
        headers = {
            "authorization": f"Bearer {token}",
            "appkey": KIS_APP_KEY,
            "appsecret": KIS_APP_SECRET,
            "tr_id": tr_id,
            "Content-Type": "application/json"
        }
        r = requests.get(
            f"https://openapi.koreainvestment.com:9443{path}",
            headers=headers, params=params, timeout=6)
        return r.json()
    except:
        return {}


def get_yahoo_quote(symbol):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://finance.yahoo.com/",
        "Origin": "https://finance.yahoo.com",
    }
    for base in ["query1", "query2"]:
        for ver in ["v8", "v7"]:
            try:
                if ver == "v8":
                    url = f"https://{base}.finance.yahoo.com/v8/finance/chart/{symbol}"
                    r = requests.get(url, headers=headers, timeout=10)
                    meta = r.json()["chart"]["result"][0]["meta"]
                    price = round(meta.get("regularMarketPrice", 0), 2)
                    prev = meta.get("chartPreviousClose") or meta.get("previousClose", price)
                else:
                    url = f"https://{base}.finance.yahoo.com/v7/finance/quote?symbols={symbol}"
                    r = requests.get(url, headers=headers, timeout=10)
                    q = r.json()["quoteResponse"]["result"][0]
                    price = round(q.get("regularMarketPrice", 0), 2)
                    prev = q.get("regularMarketPreviousClose", price)
                chg = round((price - prev) / prev * 100, 2) if prev else 0
                if price > 0:
                    return {"value": price, "change": chg}
            except:
                continue
    return {"value": None, "change": None}


def get_korean_market():
    result = {}
    for mkt, code in [("kospi", "0001"), ("kosdaq", "1001")]:
        d = kis_get("/uapi/domestic-stock/v1/quotations/inquire-index-price",
                    "FHPUP02100000",
                    {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code}).get("output", {})
        raw_chg = d.get("bstp_nmix_prdy_ctrt", None)
        try:
            chg_val = float(raw_chg) if raw_chg not in (None, "", "0") else None
        except:
            chg_val = None
        raw_val = d.get("bstp_nmix_prpr", None)
        try:
            idx_val = float(raw_val) if raw_val not in (None, "") else None
        except:
            idx_val = None
        result[mkt] = {
            "value": idx_val,
            "change": chg_val,
            "foreign": None, "institution": None
        }
        sup = kis_get("/uapi/domestic-stock/v1/quotations/inquire-index-investor",
                      "FHPST01710000",
                      {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code,
                       "FID_INPUT_DATE_1": datetime.now().strftime("%Y%m%d")}).get("output", [])
        if sup and isinstance(sup, list):
            result[mkt]["foreign"] = sup[0].get("frgn_ntby_qty", None)
            result[mkt]["institution"] = sup[0].get("orgn_ntby_qty", None)
            result[mkt]["individual"] = sup[0].get("indv_ntby_qty", None)

    result["futures_auto"] = None

    return result


# 코스피 업종 ETF (Yahoo Finance 심볼)
SECTOR_ETFS = [
    ("반도체",   "091160.KS"),
    ("2차전지",  "305720.KS"),
    ("바이오",   "207920.KS"),
    ("금융",     "139270.KS"),
    ("에너지",   "117700.KS"),
    ("방산",     "471550.KS"),
    ("조선",     "466920.KS"),
    ("자동차",   "091180.KS"),
    ("건설",     "102960.KS"),
    ("IT",       "148020.KS"),
]

def get_sector_data():
    result = []
    for name, symbol in SECTOR_ETFS:
        q = get_yahoo_quote(symbol)
        if q["change"] is not None:
            result.append({"name": name, "change": q["change"]})
    result.sort(key=lambda x: x["change"], reverse=True)
    return {"top5": result[:5], "bot5": list(reversed(result[-5:]))}


def get_global_market():
    syms = {
        "nasdaq": "%5EIXIC", "dow": "%5EDJI", "sp500": "%5EGSPC",
        "wti": "CL%3DF", "brent": "BZ%3DF",
        "usdkrw": "USDKRW%3DX",
        "vix": "%5EVIX", "ewy": "EWY",
        "tnx": "%5ETNX", "sox": "%5ESOX",
        "dxy": "DX-Y.NYB", "gold": "GC%3DF", "dram": "SOXX"
    }
    return {k: get_yahoo_quote(v) for k, v in syms.items()}


# 자동 청소 — type별로 유지할 최신 행 개수
# 이미지를 base64로 담는 무거운 타입은 1개만 유지하여 DB 폭증 방지
POST_TYPE_KEEP = {
    "mindmap": 1,    # 이미지 base64 포함 (행당 평균 700KB+)
    "wdaebon": 3,    # 보통 텍스트지만 보수적으로 3개
    "checkpoint": 1, # 사용자가 매일 갱신, 최신만 필요
    "closing": 1,    # 마감일지 — 봇이 매일 새로, 최신만 필요
}

def save_post(t, content, date):
    conn = get_db()
    conn.run(
        "INSERT INTO posts (type,content,date,created_at) VALUES (:t,:c,:d,:ca)",
        t=t, c=content, d=date, ca=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    keep = POST_TYPE_KEEP.get(t)
    if keep:
        # 같은 type의 최신 keep개만 남기고 옛 행 자동 삭제 (2단계 — pg8000 호환)
        try:
            keep_rows = list(conn.run(
                f"SELECT id FROM posts WHERE type=:t ORDER BY id DESC LIMIT {int(keep)}",
                t=t))
            if keep_rows:
                keep_ids_csv = ",".join(str(r[0]) for r in keep_rows)
                result = conn.run(
                    f"DELETE FROM posts WHERE type=:t AND id NOT IN ({keep_ids_csv})",
                    t=t)
                print(f"[save_post auto-cleanup] {t}: kept latest {keep}, deleted older rows")
        except Exception as e:
            print(f"[save_post auto-cleanup] ERROR {t}: {e}")
    conn.close()


def get_latest_post(t):
    conn = get_db()
    rows = conn.run("SELECT content,date FROM posts WHERE type=:t ORDER BY id DESC LIMIT 1", t=t)
    conn.close()
    return {"content": rows[0][0], "date": rows[0][1]} if rows else None


@app.route("/manifest.json")
def manifest():
    import json
    return Response(json.dumps({
        "name": "Yenny Dashboard",
        "short_name": "Yenny",
        "description": "머니플러스 방송 준비 대시보드",
        "start_url": "/",
        "display": "standalone",
        "orientation": "portrait-primary",
        "background_color": "#1a1d23",
        "theme_color": "#1a1d23",
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
        ]
    }), mimetype="application/manifest+json")


@app.route("/service-worker.js")
def sw():
    return Response("""const CACHE='yenny-v3';
self.addEventListener('install',e=>{self.skipWaiting()});
self.addEventListener('activate',e=>{
  e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener('fetch',e=>{
  // API와 POST는 캐시 안 함
  if(e.request.method!=='GET' || e.request.url.includes('/api/') || e.request.url.includes('/login')) return;
  e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)));
});""", mimetype="application/javascript")


# 간단한 SVG 아이콘
@app.route("/icon-192.png")
@app.route("/icon-512.png")
def icon():
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<rect width="512" height="512" fill="#1a1d23" rx="80"/>
<text x="256" y="330" font-size="280" text-anchor="middle" font-family="sans-serif">🎙️</text>
</svg>'''
    return Response(svg, mimetype="image/svg+xml")

@app.route("/")
@requires_auth
def index():
    html = get_html()
    # JS에서 사용할 API_SECRET을 head에 주입
    secret_script = f'<script>window._API_SECRET="{API_SECRET}";</script>'
    html = html.replace('</head>', f'{secret_script}</head>', 1)
    return Response(html, mimetype="text/html")


@app.route("/mindmap")
@requires_auth
def mindmap_page():
    html = get_mindmap_html()
    secret_script = f'<script>window._API_SECRET="{API_SECRET}";</script>'
    html = html.replace('</head>', f'{secret_script}</head>', 1)
    return Response(html, mimetype="text/html")


@app.route("/onair")
@requires_auth
def wandaebon_page():
    html = get_wandaebon_html()
    secret_script = f'<script>window._API_SECRET="{API_SECRET}";</script>'
    html = html.replace('</head>', f'{secret_script}</head>', 1)
    return Response(html, mimetype="text/html")


@app.route("/onair/upload", methods=["POST"])
@requires_auth
def wandaebon_upload():
    """docx 파일 받아서 파싱 후 JSON 반환."""
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "파일 없음"}), 400
    try:
        data = parse_wandaebon_docx(f.read())
        return jsonify(data)
    except Exception as e:
        print(f"[onair parse error] {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/market")
@requires_auth
def api_market():
    g = get_global_market()
    k = get_korean_market()
    return jsonify({**g, **k})


@app.route("/api/sector")
@requires_auth
def api_sector():
    return jsonify(get_sector_data())


@app.route("/api/post/<pt>")
@requires_auth
def api_get_post(pt):
    valid = ("checkpoint", "closing", "briefing", "futures", "aftermarket", "report", "report_up", "report_dn", "report_feature", "note", "todo", "calendar", "memo", "report", "wdaebon", "mindmap")
    if pt not in valid:
        return jsonify({"error": "invalid"}), 400
    return jsonify(get_latest_post(pt) or {})


@app.route("/debug/<pt>")
@requires_auth
def debug_post(pt):
    """원본 텍스트 디버그용 - 카드 파싱 안 될 때 원본 확인"""
    valid = ("checkpoint", "closing", "briefing", "wdaebon", "mindmap")
    if pt not in valid:
        return Response("invalid", 400)
    data = get_latest_post(pt) or {}
    content = data.get("content", "")
    date = data.get("date", "")
    # 보이지 않는 문자도 확인 가능하도록
    import json as _json
    return Response(
        f"<html><head><meta charset='utf-8'><title>{pt} debug</title></head><body style='font-family:monospace;padding:20px;'>"
        f"<h2>{pt} (date: {date})</h2>"
        f"<p>길이: {len(content)} chars</p>"
        f"<pre style='background:#f5f5f5;padding:15px;border:1px solid #ddd;white-space:pre-wrap;'>{content.replace('<','&lt;')}</pre>"
        f"<h3>JSON encoded (특수문자 확인용)</h3>"
        f"<pre style='background:#fff5f5;padding:15px;border:1px solid #ddd;white-space:pre-wrap;font-size:11px;'>{_json.dumps(content, ensure_ascii=False)}</pre>"
        f"</body></html>",
        mimetype="text/html"
    )


@app.route("/api/post/checkpoint/replace", methods=["POST"])
def api_save_checkpoint_replace():
    """사용자 편집 전용 — append 로직 절대 안 발동, 무조건 replace.
    
    fetch wrapper가 X-API-Secret을 자동으로 붙여서 서버가 봇/사용자 구분이 깨지는
    문제를 우회하기 위한 명시적 경로.
    """
    auth = request.authorization
    bot_secret = request.headers.get("X-API-Secret", "")
    is_bot = (bot_secret == API_SECRET)
    if not is_bot and (not auth or auth.password != DASHBOARD_PASSWORD):
        return jsonify({"error": "unauthorized"}), 401
    body = request.json or {}
    content = body.get("content", "")
    date = body.get("date", datetime.now().strftime("%Y-%m-%d"))
    # 빈 본문도 허용 (초기화 의도)
    save_post("checkpoint", content, date)
    print(f"[/api/post/checkpoint/replace] saved {len(content)} chars, date={date}")
    return jsonify({"ok": True, "saved_len": len(content)})


@app.route("/api/post/<pt>", methods=["POST"])
def api_save_post(pt):
    valid = ("checkpoint", "closing", "briefing", "futures", "aftermarket", "report", "report_up", "report_dn", "report_feature", "note", "todo", "calendar", "memo", "report", "wdaebon", "mindmap")
    if pt not in valid:
        return jsonify({"error": "invalid"}), 400
    # 대시보드 직접 저장은 인증 필요
    auth = request.authorization
    bot_secret = request.headers.get("X-API-Secret", "")
    is_bot = (bot_secret == API_SECRET)
    if not is_bot and (not auth or auth.password != DASHBOARD_PASSWORD):
        return jsonify({"error": "unauthorized"}), 401
    body = request.json or {}
    content = body.get("content", "")
    date = body.get("date", datetime.now().strftime("%Y-%m-%d"))
    if not content:
        # mindmap/checkpoint/closing은 빈 콘텐츠 저장 허용 (초기화용)
        if pt not in ("mindmap", "checkpoint", "closing"):
            return jsonify({"error": "content required"}), 400

    # 체크포인트: 봇이 보내는 메시지는 mode 플래그로 동작 결정
    #   mode="append" → 기존 본문 끝에 조각 추가 (조각 방식)
    #   mode="replace" 또는 없음 → 통째로 덮어쓰기
    # 사용자 편집은 /api/post/checkpoint/replace 별도 endpoint 사용.
    if pt == "checkpoint" and is_bot and body.get("mode") == "append":
        try:
            _c = get_db()
            _rows = list(_c.run(
                "SELECT content FROM posts WHERE type='checkpoint' ORDER BY id DESC LIMIT 1"
            ))
            _c.close()
            if _rows:
                _old_content = _rows[0][0] or ""
                _new_block = (content or "").strip()
                _old_block = _old_content.rstrip()
                if _new_block and _old_block:
                    content = _old_block + "\n\n" + _new_block
                # 기존 본문 없으면 새 본문이 그대로 content (덮어쓰기 효과)
        except Exception as _e:
            print(f"[checkpoint append] {_e}")

    save_post(pt, content, date)
    return jsonify({"ok": True})
@app.route("/api/news")
@requires_auth
def api_news():
    import xml.etree.ElementTree as ET
    feeds = [
        ("한경 속보", "https://www.hankyung.com/feed/breaking-news"),
        ("한경 경제", "https://www.hankyung.com/feed/economy"),
        ("한경 증권", "https://www.hankyung.com/feed/stock"),
        ("한경 금융", "https://www.hankyung.com/feed/finance"),
        ("한경 국제", "https://www.hankyung.com/feed/international"),
        ("매일경제", "https://www.mk.co.kr/rss/30000001/"),
        ("매일경제 증권", "https://www.mk.co.kr/rss/30100041/"),
        ("파이낸셜뉴스", "https://www.fnnews.com/rss/fn_economy_rss.xml"),
        ("머니투데이", "https://rss.mt.co.kr/mt_finance.xml"),
        ("구글 경제뉴스", "https://news.google.com/rss/search?q=%EA%B2%BD%EC%A0%9C+%EC%A6%9D%EA%B6%8C&hl=ko&gl=KR&ceid=KR:ko"),
    ]
    items = []
    for source, url in feeds:
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
            root = ET.fromstring(r.content)
            ns = {"dc": "http://purl.org/dc/elements/1.1/"}
            for item in root.findall(".//item")[:5]:
                title = item.findtext("title", "").strip()
                link = item.findtext("link", "").strip()
                pub = item.findtext("pubDate", "").strip()
                if title and link:
                    items.append({"title": title, "link": link, "pub": pub, "source": source})
        except Exception as e:
            pass
    items.sort(key=lambda x: x["pub"], reverse=True)
    return jsonify({"items": items[:25]})


@app.route("/api/theme/prices")
@requires_auth
def theme_prices():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "테마명 없음"}), 400

    SHEET_ID = os.environ.get("SHEET_ID", "")
    if not SHEET_ID:
        return jsonify({"error": "SHEET_ID 없음"}), 500

    import csv, io as _io
    sheet_url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv"
    try:
        res = requests.get(sheet_url, timeout=10)
        res.encoding = "utf-8"
        data = list(csv.DictReader(_io.StringIO(res.text)))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    seen = set()
    stocks = []
    for r in data:
        if query in r.get("테마", ""):
            code = r.get("종목코드", "").strip()
            name = r.get("종목명", "").strip()
            theme = r.get("테마", "").strip()
            if code and name and code not in seen:
                seen.add(code)
                stocks.append({"code": code, "name": name, "theme": theme})

    if not stocks:
        return jsonify({"stocks": [], "query": query})

    results = []
    for s in stocks:
        try:
            r = requests.get(
                f"https://m.stock.naver.com/api/stock/{s['code']}/basic",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
            if r.status_code == 200:
                d = r.json()
                price = d.get("closePrice") or d.get("currentPrice", "")
                pct = d.get("fluctuationsRatio", "")
                change = d.get("compareToPreviousClosePrice", "")
                try:
                    pct_f = float(str(pct).replace(",",""))
                    price_i = int(str(price).replace(",",""))
                    change_i = int(str(change).replace(",",""))
                    arrow = "▲" if pct_f >= 0 else "▼"
                    sign = "+" if pct_f >= 0 else ""
                    results.append({
                        "code": s["code"],
                        "name": s["name"],
                        "theme": s["theme"],
                        "price": f"{price_i:,}",
                        "change": f"{arrow} {abs(change_i):,}",
                        "pct": f"{sign}{pct_f:.2f}%",
                        "up": pct_f >= 0
                    })
                except:
                    results.append({"code": s["code"], "name": s["name"], "theme": s["theme"], "price": str(price), "change": "", "pct": "", "up": None})
        except:
            results.append({"code": s["code"], "name": s["name"], "theme": s["theme"], "price": "—", "change": "", "pct": "", "up": None})

    results.sort(key=lambda x: float(x["pct"].replace("%","").replace("+","") or 0), reverse=True)
    return jsonify({"stocks": results, "query": query, "count": len(results)})


@app.route("/api/kstock/search")
@requires_auth
def kstock_search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "검색어 없음"}), 400

    SHEET_ID = os.environ.get("SHEET_ID", "")
    NAVER_ID = os.environ.get("NAVER_CLIENT_ID", "")
    NAVER_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")

    if not SHEET_ID:
        return jsonify({"error": "SHEET_ID 환경변수 없음"}), 500

    try:
        import csv, io as _io
        sheet_url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv"
        res = requests.get(sheet_url, timeout=10)
        res.encoding = "utf-8"
        data = list(csv.DictReader(_io.StringIO(res.text)))

        stock_hits = [r for r in data if r.get("종목명", "").strip() == query.strip()]
        if stock_hits:
            theme_hits = []
        else:
            theme_hits = [r for r in data if query in r.get("테마", "")]

        def fetch_price(code):
            if not code:
                return None
            try:
                pr = requests.get(
                    f"https://m.stock.naver.com/api/stock/{code}/basic",
                    headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
                pd = pr.json()
                price = pd.get("closePrice") or pd.get("currentPrice", "")
                change = pd.get("compareToPreviousClosePrice", "")
                pct = pd.get("fluctuationsRatio", "")
                if not price:
                    return None
                pct_f = float(str(pct).replace(",", "") or 0)
                change_i = int(str(change).replace(",", "") or 0)
                price_i = int(str(price).replace(",", "") or 0)
                sign = "+" if pct_f >= 0 else ""
                return {
                    "price": f"{price_i:,}",
                    "change": f"{sign}{change_i:,}",
                    "pct": f"{sign}{pct_f:.2f}",
                    "up": pct_f >= 0
                }
            except:
                return None

        stock_cards = []
        if stock_hits:
            from collections import OrderedDict
            grouped = OrderedDict()
            for r in stock_hits:
                name = r.get("종목명", "").strip()
                grouped.setdefault(name, []).append(r)

            for name, rows in grouped.items():
                code = rows[0].get("종목코드", "").strip()
                themes = [
                    {"theme": r.get("테마", "").strip(), "desc": r.get("특징", "").strip()}
                    for r in rows
                ]
                stock_cards.append({
                    "name": name,
                    "code": code,
                    "themes": themes,
                    "price_info": fetch_price(code),
                    "naver_url": f"https://m.stock.naver.com/domestic/stock/{code}/total" if code else "",
                    "chart_url": f"https://m.stock.naver.com/domestic/stock/{code}/chart" if code else ""
                })

        theme_cards = []
        for r in theme_hits:
            theme_cards.append({
                "name": r.get("종목명", "").strip(),
                "code": r.get("종목코드", "").strip(),
                "theme": r.get("테마", "").strip(),
                "desc": r.get("특징", "").strip()
            })

        news_latest = []
        news_relevant = []
        if NAVER_ID and (stock_hits or theme_hits):
            try:
                import re as _re
                def clean(t): return _re.sub(r"<[^>]+>", "", t or "")
                nr_date = requests.get(
                    "https://openapi.naver.com/v1/search/news.json",
                    headers={"X-Naver-Client-Id": NAVER_ID, "X-Naver-Client-Secret": NAVER_SECRET},
                    params={"query": query, "display": 3, "sort": "date"}, timeout=8)
                date_items = nr_date.json().get("items", [])
                nr_sim = requests.get(
                    "https://openapi.naver.com/v1/search/news.json",
                    headers={"X-Naver-Client-Id": NAVER_ID, "X-Naver-Client-Secret": NAVER_SECRET},
                    params={"query": query, "display": 5, "sort": "sim"}, timeout=8)
                sim_items = nr_sim.json().get("items", [])
                date_links = {it.get("link") for it in date_items}
                sim_unique = [n for n in sim_items if n.get("link") not in date_links][:2]

                news_latest = [
                    {"title": clean(it.get("title", "")), "link": it.get("link", ""), "pub": it.get("pubDate", "")}
                    for it in date_items
                ]
                news_relevant = [
                    {"title": clean(it.get("title", "")), "link": it.get("link", "")}
                    for it in sim_unique
                ]
            except:
                pass

        return jsonify({
            "query": query,
            "found": bool(stock_hits or theme_hits),
            "stock_cards": stock_cards,
            "theme_cards": theme_cards,
            "news_latest": news_latest,
            "news_relevant": news_relevant
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/todo/clear", methods=["POST"])
@requires_auth
def clear_todo():
    save_post("todo", "", datetime.now().strftime("%Y-%m-%d"))
    return jsonify({"ok": True})


@app.route("/api/encyclopedia/search")
@requires_auth
def encyclopedia_search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "검색어 없음"}), 400

    ENC_SHEET_ID = "1_2hIjvp2MqjHNLcv0JTn1yWgC4vB5zIzwkUS_c4cqUs"
    import csv, io as _io, re
    terms = [t.strip() for t in re.split(r'[/,\s]+', query) if t.strip()]

    try:
        url = f"https://docs.google.com/spreadsheets/d/{ENC_SHEET_ID}/export?format=csv"
        r = requests.get(url, timeout=10)
        r.encoding = "utf-8"
        rows = list(csv.DictReader(_io.StringIO(r.text)))
        matched = []
        for row in rows:
            row_text = " ".join(str(v) for v in row.values())
            if all(term in row_text for term in terms):
                matched.append(row)
        return jsonify({"results": matched, "query": query})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/pdf/upload", methods=["POST"])
@requires_auth
def pdf_upload():
    from datetime import datetime as dt
    body = request.json or {}
    name = body.get("name", "report.pdf")
    data = body.get("data", "")
    if not data:
        return jsonify({"error": "파일 없음"}), 400
    conn = get_db()
    rows = conn.run("SELECT id FROM pdfs ORDER BY created_at ASC")
    if rows and len(rows) >= 3:
        conn.run("DELETE FROM pdfs WHERE id=:i", i=rows[0][0])
    conn.run("INSERT INTO pdfs (name, data, created_at) VALUES (:n,:d,:c)",
             n=name, d=data, c=dt.now().isoformat())
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/pdf/list")
@requires_auth
def pdf_list():
    conn = get_db()
    rows = conn.run("SELECT id, name, created_at FROM pdfs ORDER BY created_at DESC")
    conn.close()
    if not rows:
        return jsonify({"pdfs": []})
    return jsonify({"pdfs": [{"id": r[0], "name": r[1], "date": r[2][:10] if r[2] else ""} for r in rows]})


@app.route("/api/pdf/<int:pdf_id>")
@requires_auth
def pdf_get(pdf_id):
    conn = get_db()
    rows = conn.run("SELECT name, data FROM pdfs WHERE id=:i", i=pdf_id)
    conn.close()
    if not rows:
        return jsonify({"error": "없음"}), 404
    return jsonify({"name": rows[0][0], "data": rows[0][1]})


@app.route("/api/pdf/<int:pdf_id>/delete", methods=["POST"])
@requires_auth
def pdf_delete(pdf_id):
    conn = get_db()
    conn.run("DELETE FROM pdfs WHERE id=:i", i=pdf_id)
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/encyc/search")
@requires_auth
def encyc_search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "검색어 없음"}), 400

    ENCYC_SHEET_ID = os.environ.get("ENCYC_SHEET_ID", "")
    if not ENCYC_SHEET_ID:
        return jsonify({"error": "ENCYC_SHEET_ID 없음"}), 500

    import csv, io as _io, re
    sheet_url = f"https://docs.google.com/spreadsheets/d/{ENCYC_SHEET_ID}/export?format=csv"
    terms = [t.strip() for t in re.split(r'[/,\s]+', query) if t.strip()]

    try:
        r = requests.get(sheet_url, timeout=10)
        r.encoding = "utf-8"
        rows = list(csv.DictReader(_io.StringIO(r.text)))
        matched = []
        for row in rows:
            row_text = " ".join(str(v) for v in row.values())
            if all(term in row_text for term in terms):
                matched.append(row)
        return jsonify({"results": matched, "query": query})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/db-stats")
@requires_auth
def db_stats():
    """DB 사용량 진단 — type별 row 수, 총 크기, 평균/최대 크기"""
    conn = get_db()
    try:
        rows = list(conn.run("""
            SELECT type,
                   COUNT(*)::int as cnt,
                   SUM(LENGTH(content))::bigint as total_bytes,
                   AVG(LENGTH(content))::bigint as avg_bytes,
                   MAX(LENGTH(content))::bigint as max_bytes
            FROM posts
            GROUP BY type
            ORDER BY total_bytes DESC NULLS LAST
        """))
        # row는 (type, cnt, total, avg, max) 튜플
        def mb(n): return round((n or 0)/1024/1024, 2)
        result = []
        grand_total = 0
        for r in rows:
            t, cnt, total, avg, mx = r
            grand_total += (total or 0)
            result.append({
                "type": t,
                "rows": cnt,
                "total_MB": mb(total),
                "avg_KB": round((avg or 0)/1024, 1),
                "max_MB": mb(mx),
            })
        # 마인드맵 중복 분석 — 최신 1개 빼고 얼마나 회수 가능한지
        mm_recoverable = 0
        try:
            mm_rows = list(conn.run("""
                SELECT id, LENGTH(content)::bigint FROM posts
                WHERE type='mindmap' ORDER BY id DESC
            """))
            if len(mm_rows) > 1:
                # 최신 1개 제외 합
                mm_recoverable = sum(r[1] for r in mm_rows[1:])
        except Exception:
            pass
        return jsonify({
            "by_type": result,
            "grand_total_MB": mb(grand_total),
            "mindmap_recoverable_MB_if_keep_latest_1": mb(mm_recoverable),
        })
    finally:
        conn.close()


@app.route("/admin/db-vacuum")
@requires_auth
def db_vacuum():
    """VACUUM FULL — DELETE된 dead tuple 디스크 공간 실제 회수.
    
    안전:
    - GET 호출은 미리보기(현재 크기만 표시)
    - ?confirm=YES 있을 때만 실제 VACUUM 실행
    - posts 테이블만 대상 (이미지가 쌓이는 곳)
    """
    import time
    is_real = (request.args.get("confirm") == "YES")
    conn = get_db()
    try:
        # 현재 크기 측정
        before_rows = list(conn.run(
            "SELECT pg_size_pretty(pg_database_size(current_database())), "
            "pg_size_pretty(pg_total_relation_size('posts')), "
            "pg_database_size(current_database()), "
            "pg_total_relation_size('posts')"
        ))
        db_size_pretty, posts_size_pretty, db_size_bytes, posts_size_bytes = before_rows[0]
        before = {
            "db_size": db_size_pretty,
            "posts_table_size": posts_size_pretty,
            "db_size_MB": round(db_size_bytes/1024/1024, 2),
            "posts_table_size_MB": round(posts_size_bytes/1024/1024, 2),
        }
        if not is_real:
            return jsonify({
                "dry_run": True,
                "before": before,
                "to_run_vacuum": "?confirm=YES 추가",
                "note": "VACUUM FULL은 테이블 락을 걸어요 (몇 초~십수 초). 그 동안 대시보드 잠시 느려질 수 있음.",
            })
        # 실제 VACUUM FULL — autocommit 필요 (트랜잭션 안에서 못 돌림)
        # pg8000: autocommit on
        try:
            conn.autocommit = True
        except Exception:
            pass
        start = time.time()
        conn.run("VACUUM (FULL, ANALYZE) posts")
        elapsed = time.time() - start
        # 후 크기
        after_rows = list(conn.run(
            "SELECT pg_size_pretty(pg_database_size(current_database())), "
            "pg_size_pretty(pg_total_relation_size('posts')), "
            "pg_database_size(current_database()), "
            "pg_total_relation_size('posts')"
        ))
        ad, ap, adb, apb = after_rows[0]
        return jsonify({
            "vacuum_done": True,
            "elapsed_seconds": round(elapsed, 2),
            "before": before,
            "after": {
                "db_size": ad,
                "posts_table_size": ap,
                "db_size_MB": round(adb/1024/1024, 2),
                "posts_table_size_MB": round(apb/1024/1024, 2),
            },
            "freed_MB": round((db_size_bytes - adb)/1024/1024, 2),
        })
    finally:
        try: conn.close()
        except Exception: pass


@app.route("/admin/db-cleanup-all")
@requires_auth
def db_cleanup_all():
    """모든 POST_TYPE_KEEP type을 한 번에 정리.
    
    각 type별로 최신 keep개만 남기고 옛 행 삭제.
    자동 청소 로직과 동일한 동작을 명시적으로 한 번에 트리거.
    """
    results = {}
    conn = get_db()
    try:
        for t, keep in POST_TYPE_KEEP.items():
            try:
                # 정리 전 카운트
                before_rows = list(conn.run("SELECT COUNT(*) FROM posts WHERE type=:t", t=t))
                before_cnt = before_rows[0][0] if before_rows else 0
                
                # 최신 keep개 id 가져오기
                keep_rows = list(conn.run(
                    f"SELECT id FROM posts WHERE type=:t ORDER BY id DESC LIMIT {int(keep)}",
                    t=t))
                if keep_rows:
                    keep_ids_csv = ",".join(str(r[0]) for r in keep_rows)
                    conn.run(
                        f"DELETE FROM posts WHERE type=:t AND id NOT IN ({keep_ids_csv})",
                        t=t)
                
                # 정리 후 카운트
                after_rows = list(conn.run("SELECT COUNT(*) FROM posts WHERE type=:t", t=t))
                after_cnt = after_rows[0][0] if after_rows else 0
                
                results[t] = {
                    "kept_limit": keep,
                    "before_rows": before_cnt,
                    "after_rows": after_cnt,
                    "deleted_rows": before_cnt - after_cnt,
                }
            except Exception as e:
                results[t] = {"error": str(e)}
    finally:
        conn.close()
    return jsonify({"cleanup_done": True, "results": results})


@app.route("/debug/closing")
@requires_auth
def debug_closing():
    """마감일지 마지막 entry들 확인 — 언제 멈췄는지 진단용."""
    conn = get_db()
    try:
        rows = list(conn.run(
            "SELECT id, date, created_at, LENGTH(content) "
            "FROM posts WHERE type='closing' ORDER BY id DESC LIMIT 15"
        ))
    finally:
        conn.close()
    return jsonify({
        "type": "closing",
        "total_recent_shown": len(rows),
        "entries": [
            {"id": r[0], "date": r[1], "created_at": str(r[2]), "content_len": r[3]}
            for r in rows
        ]
    })


@app.route("/admin/db-cleanup-mindmap", methods=["GET", "POST"])
@requires_auth
def db_cleanup_mindmap():
    """마인드맵 type의 과거 스냅샷 삭제 (최신 N개만 유지). 다른 type은 안 건드림.
    
    안전 장치:
    - GET 요청은 무조건 dry-run (실제 삭제 X)
    - POST + confirm=YES 일 때만 진짜 삭제
    """
    keep = max(1, int(request.args.get("keep", "3")))
    is_real = (request.args.get("confirm") == "YES")
    conn = get_db()
    try:
        to_delete = list(conn.run("""
            SELECT id, LENGTH(content)::bigint FROM posts
            WHERE type='mindmap'
            AND id NOT IN (
                SELECT id FROM posts WHERE type='mindmap' ORDER BY id DESC LIMIT :k
            )
            ORDER BY id
        """, k=keep))
        bytes_to_free = sum(r[1] for r in to_delete)
        count = len(to_delete)
        if not is_real:
            return jsonify({
                "dry_run": True,
                "would_delete_rows": count,
                "would_free_MB": round(bytes_to_free/1024/1024, 2),
                "keep_latest": keep,
                "to_actually_delete": "POST 요청 + ?confirm=YES 추가",
            })
        if count > 0:
            ids_csv = ",".join(str(r[0]) for r in to_delete)
            conn.run(f"DELETE FROM posts WHERE id IN ({ids_csv})")
        return jsonify({
            "deleted_rows": count,
            "freed_MB": round(bytes_to_free/1024/1024, 2),
            "kept_latest": keep,
            "note": "디스크 실제 회수는 PG autovacuum이 처리 (몇 분~수 시간).",
        })
    finally:
        conn.close()


@app.route("/api/cgdb/search")
@requires_auth
def cgdb_search():
    """CG DB 시트(날짜·CG 제목·키워드·유형)에서 키워드 검색"""
    query = request.args.get("q", "").strip()
    CG_DB_SHEET_ID = os.environ.get("CG_DB_SHEET_ID", "")
    CG_DB_GID = os.environ.get("CG_DB_GID", "0").strip() or "0"
    if not CG_DB_SHEET_ID:
        return jsonify({"error": "CG_DB_SHEET_ID 환경변수 없음"}), 500

    import csv, io as _io, re
    sheet_url = f"https://docs.google.com/spreadsheets/d/{CG_DB_SHEET_ID}/export?format=csv&gid={CG_DB_GID}"

    try:
        r = requests.get(sheet_url, timeout=10)
        r.encoding = "utf-8"
        rows = list(csv.DictReader(_io.StringIO(r.text)))
        if not query:
            # 검색어 없으면 최신 30행만 (시트가 이미 최신순)
            results = rows[:30]
        else:
            terms = [t.strip().lower() for t in re.split(r'[/,\s]+', query) if t.strip()]
            results = []
            for row in rows:
                row_text = " ".join(str(v or "") for v in row.values()).lower()
                if all(term in row_text for term in terms):
                    results.append(row)
            results = results[:80]  # 시트 순서 그대로 (최신 우선), 최대 80건
        return jsonify({"results": results, "query": query, "total": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@requires_auth
def encyc_add():
    """대시보드에서 입력한 새 표현을 Apps Script Webhook을 통해 시트에 append."""
    data = request.get_json(silent=True) or {}
    expr = (data.get("표현") or "").strip()
    if not expr:
        return jsonify({"error": "표현은 필수에요"}), 400
    situation = (data.get("상황") or "").strip()
    topic = (data.get("주제") or "").strip()

    webhook_url = os.environ.get("ENCYC_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return jsonify({"error": "ENCYC_WEBHOOK_URL 환경변수가 없어요 (Apps Script 배포 URL 필요)"}), 500

    webhook_secret = os.environ.get("ENCYC_WEBHOOK_SECRET", "anchoryen")
    payload = {
        "secret": webhook_secret,
        "상황": situation,
        "주제": topic,
        "표현": expr,
    }
    try:
        r = requests.post(webhook_url, json=payload, timeout=10)
        # Apps Script가 JSON으로 응답하면 파싱, 아니면 단순 OK
        try:
            j = r.json()
            if isinstance(j, dict) and j.get("error"):
                return jsonify({"error": "시트 측 오류: " + str(j["error"])}), 500
        except Exception:
            pass
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": "Webhook 호출 실패: " + str(e)}), 500


@app.route("/api/guest/search")
@requires_auth
def guest_search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "검색어 없음"}), 400

    GUEST_SHEET_ID = os.environ.get("GUEST_SHEET_ID", "")
    if not GUEST_SHEET_ID:
        return jsonify({"error": "GUEST_SHEET_ID 없음"}), 500

    import csv, io as _io
    base_url = f"https://docs.google.com/spreadsheets/d/{GUEST_SHEET_ID}/export?format=csv&gid="

    sheets = [
        ("Master", "437534302"),
        ("History", "1633570539"),
        ("골든픽", "1460063403"),
        ("오늘장전략", "1283113183"),
    ]

    import re
    terms = [t.strip() for t in re.split(r'[/,\s]+', query) if t.strip()]

    results = {}
    for sheet_name, gid in sheets:
        try:
            r = requests.get(base_url + gid, timeout=10)
            r.encoding = "utf-8"
            rows = list(csv.DictReader(_io.StringIO(r.text)))
            matched = []
            for row in rows:
                row_text = " ".join(str(v) for v in row.values())
                if all(term in row_text for term in terms):
                    matched.append(row)
            if matched:
                def get_date_key(row):
                    date_val = row.get("날짜", "") or row.get("date", "")
                    return str(date_val)
                matched.sort(key=get_date_key, reverse=True)
                results[sheet_name] = matched
        except:
            pass

    return jsonify({"results": results, "query": query, "terms": terms})


@app.route("/api/calendar/parse", methods=["POST"])
@requires_auth
def calendar_parse():
    body = request.json or {}
    raw = body.get("content", "")
    if not raw:
        return jsonify({"error": "내용 없음"}), 400
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": f"""아래는 주간 증시 일정 텍스트입니다.
날짜별로 파싱해서 반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 절대 포함하지 마세요.

{{
  "week_label": "4월 11일~17일",
  "days": [
    {{
      "date": "4월 11일 토요일",
      "day_key": "sat",
      "items": ["美-이란, 파키스탄서 협상 개시", "푸틴, 우크라 전쟁 부활절 휴전 선언"],
      "guests": ["반종민 소장 - 주도주를 만드는 이슈", "박종현 대표 - 가는 종목이 더 간다"]
    }}
  ]
}}

규칙:
- day_key는 mon/tue/wed/thu/fri/sat/sun 중 하나
- items: 경제·증권 일정만. 핵심 한 줄 요약. 세부내용 생략.
- guests: 출연자 이름과 주제가 있으면 guests 배열에. "이름 - 주제" 형식. 출연자가 없으면 빈 배열 [].
- 출연자처럼 보이는 항목(사람 이름 + 주제/소속)은 반드시 guests로 분류.

텍스트:
{raw}"""}]
        )
        import json
        text = msg.content[0].text.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)
        save_post("calendar", json.dumps(data, ensure_ascii=False), datetime.now().strftime("%Y-%m-%d"))
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/calendar/update", methods=["POST"])
@requires_auth
def calendar_update():
    import json
    body = request.json or {}
    data = body.get("data", {})
    save_post("calendar", json.dumps(data, ensure_ascii=False), datetime.now().strftime("%Y-%m-%d"))
    return jsonify({"ok": True})


@app.route("/api/calendar/clear", methods=["POST"])
@requires_auth
def calendar_clear():
    save_post("calendar", "", datetime.now().strftime("%Y-%m-%d"))
    return jsonify({"ok": True})


@app.route("/api/note/clear", methods=["POST"])
@requires_auth
def clear_note():
    save_post("note", "", datetime.now().strftime("%Y-%m-%d"))
    return jsonify({"ok": True})


@app.route("/api/report/clear", methods=["POST"])
@requires_auth
def clear_report():
    for t in ("report_up","report_dn","report_feature"):
        save_post(t, "", datetime.now().strftime("%Y-%m-%d"))
    return jsonify({"ok": True})


@app.route("/api/briefing/generate", methods=["POST"])
@requires_auth
def generate_briefing():
    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY 없음"}), 500
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        today = datetime.now().strftime("%m월 %d일")
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content":
                f"현지시각 {today} 미국 증시 브리핑 해줘. CNBC, Bloomberg, WSJ 반드시 웹서치해서 "
                f"시장 주도 재료와 지수 수치, 주요 뉴스, 특징주를 한국어로 정리해줘."}]
        )
        content = " ".join([b.text for b in msg.content if hasattr(b, "text") and b.text])
        date = datetime.now().strftime("%Y-%m-%d")
        save_post("briefing", content, date)
        return jsonify({"content": content, "date": date})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
def get_html():
    return r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Yenny">
<meta name="theme-color" content="#1a1d23">
<link rel="manifest" href="/manifest.json">
<title>Yenny Dashboard</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&family=DM+Mono:wght@400;500&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Noto Sans KR',sans-serif;background:#f0f2f5;color:#1a1d23;min-height:100vh}
.topbar{display:flex;justify-content:space-between;align-items:center;padding:14px 24px;background:#1a1d23;position:sticky;top:0;z-index:10;border-bottom:3px solid #e8b84b}
.topbar-title{font-size:16px;font-weight:700;color:#fff;letter-spacing:-.3px}
.topbar-title span{color:#e8b84b;font-weight:400;font-size:13px;margin-left:6px}
#clock{font-family:'DM Mono',monospace;font-size:13px;color:#e8b84b;letter-spacing:.05em}
.container{padding:20px;max-width:1100px;margin:0 auto}
.section-label{font-size:11px;font-weight:700;color:#7a8099;letter-spacing:.12em;text-transform:uppercase;margin:24px 0 10px;padding-left:2px}
.grid-cp-note{display:grid;grid-template-columns:2fr 1fr;gap:10px}
@media (max-width:760px){
  .grid-cp-note{grid-template-columns:1fr!important}
}
.note-tab{font-size:11px;padding:4px 10px;background:#F8F7F0;border:0.5px solid #E5E1D6;border-radius:5px;cursor:pointer;color:#5F5E5A;font-family:inherit;display:inline-flex;align-items:center;gap:5px;line-height:1.4;max-width:140px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.note-tab:hover{background:#F1EFE5;border-color:#D3D1C7}
.note-tab.active{background:#1a1d23;color:#e8b84b;border-color:#1a1d23;font-weight:500}
.note-tab .close-x{font-size:11px;color:#999;cursor:pointer;padding:0 2px;border-radius:2px;line-height:1;display:none}
.note-tab:hover .close-x{display:inline}
.note-tab.active .close-x{color:#e8b84b;display:inline}
.note-tab .close-x:hover{background:rgba(255,255,255,0.18);color:#e8b84b}
.note-tab-add{font-size:11px;padding:4px 9px;background:transparent;border:0.5px dashed #C2BFB1;border-radius:5px;cursor:pointer;color:#888780;font-family:inherit}
.note-tab-add:hover{background:#FAFAF7;border-color:#5F5E5A;color:#1a1d23}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.grid-futures-todo{display:grid;grid-template-columns:1fr 2fr;gap:10px}
.grid-todo-memo{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.grid-domestic{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.grid-us{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
@media(max-width:680px){
  .grid-domestic{grid-template-columns:1fr 1fr!important;gap:8px}
  .grid-us{grid-template-columns:1fr 1fr 1fr!important;gap:6px}
  .us-4{order:12}
  .us-8{order:13}
  .us-12{order:14}
  .container{padding:12px;padding-top:max(12px, env(safe-area-inset-top));padding-bottom:max(12px, env(safe-area-inset-bottom))}
  .grid3{grid-template-columns:1fr 1fr!important;gap:8px}
  .grid4{grid-template-columns:1fr 1fr 1fr!important;gap:6px}
  .grid2{grid-template-columns:1fr!important;gap:8px}
  .grid-futures-todo, .grid-todo-memo{grid-template-columns:1fr!important;gap:8px}
  [style*="grid-template-columns:1fr 2fr"],
  [style*="grid-template-columns: 1fr 2fr"]{grid-template-columns:1fr!important}
  [style*="display:grid"][style*="grid-template-columns:1fr 1fr"]{grid-template-columns:1fr!important}
  .section-label{margin:18px 0 8px;font-size:10px}
  .content-card{padding:12px 14px}
  .card{padding:12px 10px}
  .grid4 .metric-val, .grid-us .metric-val{font-size:16px!important;letter-spacing:-0.3px}
  .grid4 .metric-label, .grid-us .metric-label{font-size:9px!important}
  .grid4 .metric-chg, .grid-us .metric-chg{font-size:11px}
  .grid3 .metric-val{font-size:22px}
  .grid3 .metric-label{font-size:10px}
  .metric-val{font-size:22px}
  .metric-label{font-size:10px}
  .content-title{font-size:14px}
  .tab{font-size:11px;padding:4px 10px}
  .btn{font-size:12px;padding:6px 10px}
  .rich-editor, textarea.input-area{font-size:14px;min-height:220px!important}
  .richtext-toolbar{overflow-x:auto;flex-wrap:nowrap;padding:4px}
  .richtext-toolbar button{min-width:28px;flex-shrink:0}
  .richtext-toolbar span[style*="flex:1"]{display:none!important}
  .richtext-toolbar span[style*="font-size:10px"]{display:none!important}
  .content-body{min-height:200px!important}
  .content-header{flex-wrap:wrap;gap:6px}
  h1, .dashboard-title{font-size:18px!important}
  .input-row{flex-direction:column}
  .input-row .btn{width:100%}
}
.card{background:#fff;border-radius:14px;padding:16px 18px;box-shadow:0 1px 3px rgba(0,0,0,.07),0 4px 16px rgba(0,0,0,.04)}
.metric-label{font-size:11px;font-weight:700;color:#7a8099;letter-spacing:.08em;text-transform:uppercase;margin-bottom:8px}
.metric-val{font-size:26px;font-weight:700;color:#1a1d23;letter-spacing:-.5px;font-family:'DM Mono',monospace}
.metric-chg{font-size:13px;font-weight:600;margin-top:5px}
.metric-sup{font-size:12px;color:#7a8099;margin-top:8px;display:flex;gap:12px}
.up{color:#d63031}
.dn{color:#0984e3}
.flat{color:#b2bec3}
.loading{color:#b2bec3;font-size:20px}
.content-card{background:#fff;border-radius:14px;padding:18px 20px;margin-bottom:10px;box-shadow:0 1px 3px rgba(0,0,0,.07),0 4px 16px rgba(0,0,0,.04)}
.content-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px}
.content-title{font-size:14px;font-weight:700;color:#1a1d23}
.content-date{font-size:11px;color:#b2bec3;font-family:'DM Mono',monospace}
.content-body{font-size:14px;color:#2d3436;line-height:1.8;white-space:pre-wrap;max-height:300px;overflow-y:auto}
.content-empty{font-size:13px;color:#b2bec3;font-style:italic}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border-radius:8px;border:1.5px solid #dfe6e9;background:#fff;color:#636e72;font-size:12px;font-weight:600;cursor:pointer;transition:all .15s;text-decoration:none}
.btn:hover{background:#f0f2f5;color:#1a1d23;border-color:#b2bec3}
.btn-primary{background:#1a1d23;border-color:#1a1d23;color:#e8b84b}
.btn-primary:hover{background:#2d3436;border-color:#2d3436}
.btn-mindmap{background:#1a1d23;border-color:#1a1d23;color:#e8b84b}
.btn-mindmap:hover{background:#2d3436;color:#fff}
.btn-green{background:#00b894;border-color:#00b894;color:#fff}
.btn-green:hover{background:#00a381}
.btn.ls{opacity:.5;pointer-events:none}
.refresh-dot{width:7px;height:7px;border-radius:50%;background:#e8b84b;display:inline-block;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.spinner{display:inline-block;width:13px;height:13px;border:2px solid #dfe6e9;border-top-color:#e8b84b;border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.etf-row{display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid #f0f2f5;font-size:13px}
.etf-row:last-child{border-bottom:none}
.etf-name{color:#2d3436;font-weight:500}
.etf-chg{font-weight:700;font-family:'DM Mono',monospace}
textarea.input-area{width:100%;background:#f8f9fa;border:1.5px solid #dfe6e9;border-radius:10px;color:#2d3436;font-size:14px;padding:12px 14px;resize:vertical;min-height:100px;font-family:'Noto Sans KR',sans-serif;line-height:1.7}
textarea.input-area:focus{outline:none;border-color:#e8b84b;background:#fff}
.rich-editor{width:100%;background:#f8f9fa;border:1.5px solid #dfe6e9;border-radius:10px;color:#2d3436;font-size:14px;padding:12px 14px;min-height:100px;font-family:'Noto Sans KR',sans-serif;line-height:1.7;overflow-y:auto;outline:none}
.rich-editor:focus{border-color:#e8b84b;background:#fff}
.rich-editor:empty::before{content:attr(data-placeholder);color:#b2bec3;pointer-events:none}
.richtext-toolbar{display:flex;gap:4px;flex-wrap:wrap;padding:6px;background:#f0f2f5;border-radius:8px;margin-bottom:6px}
.richtext-toolbar button{width:30px;height:28px;border:1px solid #dfe6e9;background:white;color:#2d3436;border-radius:5px;cursor:pointer;font-size:13px;font-weight:600;display:inline-flex;align-items:center;justify-content:center;padding:0;transition:all .1s}
.richtext-toolbar button:hover{background:#e8b84b;color:white;border-color:#e8b84b}
.richtext-toolbar .rt-sep{width:1px;background:#dfe6e9;margin:2px 2px}
input.input-line{width:100%;background:#f8f9fa;border:1.5px solid #dfe6e9;border-radius:10px;color:#2d3436;font-size:15px;font-weight:600;padding:10px 14px;font-family:'DM Mono',monospace}
input.input-line:focus{outline:none;border-color:#e8b84b;background:#fff}
.input-row{display:flex;gap:8px;align-items:flex-start;margin-top:10px}
.saved-badge{font-size:11px;color:#00b894;font-weight:700;margin-left:8px;display:none}
.tab-bar{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:12px}
.tab{padding:5px 13px;border-radius:20px;border:1.5px solid #dfe6e9;background:#f0f2f5;color:#636e72;font-size:12px;font-weight:600;cursor:pointer;transition:all .15s}
.tab.active{background:#1a1d23;border-color:#1a1d23;color:#e8b84b}
.tab:hover:not(.active){background:#fff;color:#1a1d23}
.futures-val{font-size:28px;font-weight:700;color:#1a1d23;font-family:'DM Mono',monospace;margin-bottom:12px}
.q-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px;margin-top:10px}
.q-card{background:#fff;border:0.5px solid #dfe6e9;border-left:3px solid #888;border-radius:0;padding:13px 15px;display:flex;flex-direction:column;gap:9px}
.q-card.qt-2dan{border-left-color:#888}
.q-card.qt-trust{border-left-color:#185FA5}
.q-card.qt-connect{border-left-color:#534AB7}
.q-card.qt-impact{border-left-color:#D85A30}
.q-card-head{display:flex;flex-wrap:wrap;gap:4px;align-items:center}
.q-num{font-size:11px;font-weight:500;background:#1a1d23;color:#fff;padding:2px 8px;border-radius:4px;font-family:'DM Mono',monospace}
.q-type{font-size:11px;padding:2px 8px;border-radius:4px;background:#f1efe8;color:#444}
.q-type.t-trust{background:#E6F1FB;color:#0C447C}
.q-type.t-connect{background:#EEEDFE;color:#3C3489}
.q-type.t-impact{background:#FAECE7;color:#712B13}
.q-guest{font-size:11px;padding:2px 8px;border-radius:999px;background:#fdfbf5;border:0.5px solid #e8b84b;color:#1a1d23}
.q-title{font-size:13px;font-weight:600;color:#1a1d23;line-height:1.45}
.q-meta{font-size:11px;color:#636e72;line-height:1.5}
.q-meta-icon{color:#999;margin-right:3px}
.q-body{font-size:12px;color:#2d3436;line-height:1.65;padding:9px 11px;background:#f8f9fa;border-radius:7px;outline:none;cursor:text;min-height:50px;white-space:pre-wrap}
.q-body:focus{background:#fff;box-shadow:inset 0 0 0 1.5px #e8b84b}
.q-body:empty::before{content:attr(data-placeholder);color:#b2bec3;font-style:italic}
</style>
</head>
<body>
<div class="topbar">
  <div class="topbar-title">Yenny Dashboard</div>
  <div style="display:flex;align-items:center;gap:10px;">
    <span class="refresh-dot"></span>
    <span id="clock" style="font-size:12px;color:#666;"></span>
  </div>
</div>

<div class="container">

  <!-- 국내 시장 -->
  <div class="section-label">국내 시장</div>
  <div class="grid-domestic">
    <div class="card">
      <div class="metric-label">코스피</div>
      <div class="metric-val" id="kospi-val"><span class="loading">—</span></div>
      <div class="metric-chg" id="kospi-chg"></div>
      <div class="metric-sup" id="kospi-sup"></div>
    </div>
    <div class="card">
      <div class="metric-label">코스닥</div>
      <div class="metric-val" id="kosdaq-val"><span class="loading">—</span></div>
      <div class="metric-chg" id="kosdaq-chg"></div>
      <div class="metric-sup" id="kosdaq-sup"></div>
    </div>
    <div class="card">
      <div class="metric-label">원/달러 (주간)</div>
      <div class="metric-val" id="usdkrw-val"><span class="loading">—</span></div>
      <div class="metric-chg" id="usdkrw-chg"></div>
    </div>
    <div class="content-card" style="margin-bottom:0;padding:12px 14px;">
      <div class="content-header" style="margin-bottom:6px;">
        <span class="metric-label" style="margin-bottom:0;">🌙 야간선물</span>
        <span class="saved-badge" id="futures-badge">✓</span>
      </div>
      <div id="futures-display" class="futures-val" style="font-size:22px;">—</div>
      <div id="futures-auto-val" style="font-size:10px;color:#b2bec3;margin-bottom:6px;"></div>
      <div style="display:flex;gap:4px;">
        <input class="input-line" id="futures-input" placeholder="+1.2%" style="flex:1;font-size:13px;padding:6px 10px;" />
        <button class="btn btn-green" onclick="saveFutures()" style="font-size:11px;padding:6px 10px;">저장</button>
      </div>
    </div>
  </div>

  <!-- 투두리스트 + 메모 -->
  <div class="grid-todo-memo" style="margin-top:10px;">
      <div class="content-card" style="margin-bottom:0;">
        <div class="content-header">
          <span class="content-title">✅ 투두리스트</span>
          <span class="saved-badge" id="todo-badge">✓ 저장됨</span>
        </div>
        <div id="todo-list" style="margin-bottom:8px;"></div>
        <div style="display:flex;gap:6px;">
          <input id="todo-new-input" class="input-line" placeholder="할일 추가..." style="flex:1;font-size:13px;"
            onkeydown="if(event.key==='Enter')addTodoItem()"/>
          <button class="btn btn-green" onclick="addTodoItem()" style="padding:8px 12px;">+</button>
        </div>
      </div>
      <div class="content-card" style="margin-bottom:0;">
        <div class="content-header">
          <span class="content-title">📝 메모</span>
          <div style="display:flex;gap:6px;">
            <span class="saved-badge" id="memo-badge">✓ 저장됨</span>
            <button class="btn btn-green" onclick="saveMemo()">저장</button>
            <button class="btn" onclick="clearMemo()" style="color:#d63031;border-color:#fab1a0;">↺ 초기화</button>
          </div>
        </div>
        <textarea class="input-area" id="memo-input" placeholder="메모..." style="min-height:120px;"></textarea>
      </div>
  </div>

  <!-- 미국·글로벌 -->
  <div class="section-label">미국·글로벌</div>
  <div class="grid-us">
    <div class="card us-1"><div class="metric-label">다우존스</div><div class="metric-val" id="dow-val"><span class="loading">—</span></div><div class="metric-chg" id="dow-chg"></div></div>
    <div class="card us-2"><div class="metric-label">S&amp;P 500</div><div class="metric-val" id="sp500-val"><span class="loading">—</span></div><div class="metric-chg" id="sp500-chg"></div></div>
    <div class="card us-3"><div class="metric-label">나스닥</div><div class="metric-val" id="nasdaq-val"><span class="loading">—</span></div><div class="metric-chg" id="nasdaq-chg"></div></div>
    <div class="card us-4"><div class="metric-label">EWY</div><div class="metric-val" id="ewy-val"><span class="loading">—</span></div><div class="metric-chg" id="ewy-chg"></div></div>
    <div class="card us-5"><div class="metric-label">미국 10년물 금리</div><div class="metric-val" id="tnx-val"><span class="loading">—</span></div><div class="metric-chg" id="tnx-chg"></div></div>
    <div class="card us-6"><div class="metric-label">달러인덱스 (DXY)</div><div class="metric-val" id="dxy-val"><span class="loading">—</span></div><div class="metric-chg" id="dxy-chg"></div></div>
    <div class="card us-7"><div class="metric-label">VIX</div><div class="metric-val" id="vix-val"><span class="loading">—</span></div><div class="metric-chg" id="vix-chg"></div></div>
    <div class="card us-8"><div class="metric-label">필라델피아반도체</div><div class="metric-val" id="sox-val"><span class="loading">—</span></div><div class="metric-chg" id="sox-chg"></div></div>
    <div class="card us-9"><div class="metric-label">WTI 유가</div><div class="metric-val" id="wti-val"><span class="loading">—</span></div><div class="metric-chg" id="wti-chg"></div></div>
    <div class="card us-10"><div class="metric-label">브렌트유</div><div class="metric-val" id="brent-val"><span class="loading">—</span></div><div class="metric-chg" id="brent-chg"></div></div>
    <div class="card us-11"><div class="metric-label">금</div><div class="metric-val" id="gold-val"><span class="loading">—</span></div><div class="metric-chg" id="gold-chg"></div></div>
    <div class="card us-12"><div class="metric-label">DRAM ETF (SOXX)</div><div class="metric-val" id="dram-val"><span class="loading">—</span></div><div class="metric-chg" id="dram-chg"></div></div>
  </div>
  <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:8px;align-items:center;">
    <a href="https://www.bloomberg.com/asia" target="_blank" class="btn">📰 Bloomberg</a>
    <a href="https://finviz.com/" target="_blank" class="btn">📊 Finviz</a>
    <a href="https://www.cnbc.com/watchlist/" target="_blank" class="btn">📺 CNBC Watchlist</a>
    <button class="btn" onclick="loadAll()" id="refresh-btn"><span>↻</span> 새로고침</button>
  </div>

  <!-- 업종 등락률 -->
  <div class="section-label">업종 등락률 (코스피) / 마켓맵</div>
  <div class="grid2">
    <div class="content-card" style="margin-bottom:0;display:flex;flex-direction:column;gap:0;">
      <div class="tab-bar">
        <button class="tab active" onclick="sectorTab(this,'top')" id="tab-sector-top">📈 상승</button>
        <button class="tab" onclick="sectorTab(this,'bot')" id="tab-sector-bot">📉 하락</button>
      </div>
      <div id="sector-top"><span class="content-empty">로딩 중...</span></div>
      <div id="sector-bot" style="display:none;"><span class="content-empty">로딩 중...</span></div>
      <div style="border-top:1px solid #f0f2f5;margin-top:12px;padding-top:10px;">
        <div style="font-size:11px;font-weight:700;color:#7a8099;margin-bottom:8px;letter-spacing:.08em;">📰 증권 뉴스 (한국경제 · 연합뉴스 경제 · 세계)</div>
        <div id="news-list" style="max-height:300px;overflow-y:auto;">
          <span class="content-empty">뉴스 로딩 중...</span>
        </div>
        <div style="margin-top:6px;">
          <button class="btn" onclick="loadNews()" style="font-size:11px;padding:5px 10px;">↻ 새로고침</button>
        </div>
      </div>
    </div>
    <div class="content-card" style="margin-bottom:0;padding:12px 16px 0;overflow:hidden;">
      <div class="tab-bar">
        <button class="tab active" onclick="mapTab(this,'kospi')" id="tab-map-kospi">코스피</button>
        <button class="tab" onclick="mapTab(this,'kosdaq')" id="tab-map-kosdaq">코스닥</button>
      </div>
      <div style="display:flex;gap:6px;margin-bottom:8px;">
        <a id="map-link-kospi" href="https://markets.hankyung.com/marketmap/kospi" target="_blank" class="btn btn-primary" style="font-size:11px;padding:6px 10px;">🗺 바로가기</a>
        <a id="map-link-kosdaq" href="https://markets.hankyung.com/marketmap/kosdaq" target="_blank" class="btn" style="font-size:11px;padding:6px 10px;display:none;">🗺 바로가기</a>
      </div>
      <div style="width:100%;height:380px;overflow:hidden;border-radius:8px;position:relative;">
        <iframe id="map-frame" src="https://markets.hankyung.com/marketmap/kospi"
          style="width:250%;height:1100px;border:none;position:absolute;top:-220px;left:-75%;transform:scale(0.47);transform-origin:top center;"
          scrolling="no"></iframe>
      </div>
    </div>
  </div>

  <!-- K-Stock 검색 -->
  <div class="section-label">종목 · 테마 검색</div>
  <div class="content-card">
    <div class="content-header">
      <span class="content-title">🔍 K-Stock 검색</span>
      <span style="font-size:11px;color:#b2bec3;">종목명 또는 테마명 · DB 직접 조회</span>
    </div>
    <div class="input-row">
      <input class="input-line" id="kstock-input" placeholder="예) 삼성전자 / 방산 / 2차전지" style="flex:1;" onkeydown="if(event.key==='Enter')searchKstock()"/>
      <button class="btn btn-primary" onclick="searchKstock()" id="kstock-btn">검색</button>
    </div>
    <div class="grid2" style="margin-top:14px;align-items:start;">
      <div id="kstock-result"></div>
      <div id="kstock-news"></div>
    </div>
  </div>

  <!-- 테마 시세 -->
  <div class="section-label">📈 테마 시세</div>
  <div class="content-card">
    <div class="content-header">
      <span class="content-title">📈 테마별 종목 시세</span>
      <span style="font-size:11px;color:#b2bec3;">DB 기반 일괄 조회</span>
    </div>
    <div class="input-row">
      <input class="input-line" id="theme-input" placeholder="예) 방산 / 2차전지 / 반도체" style="flex:1;"
        onkeydown="if(event.key==='Enter')searchThemePrices()"/>
      <button class="btn btn-primary" onclick="searchThemePrices()" id="theme-price-btn">조회</button>
    </div>
    <div id="theme-price-result" style="margin-top:14px;"></div>
  </div>

  <!-- 체크포인트 + 노트 2:1 분할 -->
  <div class="section-label">체크포인트</div>
  <div style="margin-bottom:10px;">
    <div class="content-card" style="margin-bottom:0;display:flex;flex-direction:column;">
      <div class="content-header">
        <span class="content-title">☑ 오늘 체크포인트</span>
        <div style="display:flex;gap:6px;align-items:center;">
          <span class="content-date" id="checkpoint-date"></span>
          <button class="btn" onclick="loadPost('checkpoint','checkpoint-body','checkpoint-date')" style="font-size:11px;padding:5px 10px;" title="서버에서 최신 본문 받아오기">↻ 새로고침</button>
          <button class="btn" onclick="enterCpEdit()" id="cp-edit-btn" style="font-size:11px;padding:5px 10px;">✏️ 편집</button>
          <button class="btn" onclick="clearCheckpoint()" style="font-size:11px;padding:5px 10px;color:#d63031;border-color:#fab1a0;" title="체크포인트 전부 비우기">🗑 초기화</button>
        </div>
      </div>
      <div class="tab-bar" id="cp-tabs">
        <button class="tab active" onclick="cpTab(this,'all')">전체</button>
        <button class="tab" onclick="cpTab(this,'indicator')">📊지표</button>
        <button class="tab" onclick="cpTab(this,'us_market')">🇺🇸美증시</button>
        <button class="tab" onclick="cpTab(this,'signal')">📡시그널</button>
        <button class="tab" onclick="cpTab(this,'sector')">📌Sector</button>
        <button class="tab" onclick="cpTab(this,'kospi')">📌코스피</button>
        <button class="tab" onclick="cpTab(this,'kosdaq')">📌코스닥</button>
      </div>
      <div class="content-body" id="checkpoint-body" style="flex:1;min-height:400px;"><span class="content-empty">텔레그램 봇으로 체크포인트를 올리면 여기에 표시됩니다.</span></div>
    </div>
  </div>

  <!-- ON AIR — 마인드맵 + 완대본 -->
  <div class="section-label">🎙️ ON AIR</div>
  <div class="content-card" style="padding:14px 18px;">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;">
      <span style="font-size:13px;color:#636e72;">오늘 방송 흐름 정리 · 완대본 메모</span>
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <a href="/mindmap" target="_blank" class="btn btn-mindmap" style="white-space:nowrap;">🗺️ 마인드맵 →</a>
        <a href="/onair" target="_blank" class="btn btn-mindmap" style="white-space:nowrap;">🎙️ ON AIR →</a>
      </div>
    </div>
  </div>

  <!-- 마감일지 -->
  <div class="section-label" style="margin-top:24px;">마감일지 / 리서치 리포트</div>
  <div class="grid2" style="align-items:start;">
    <div class="content-card" style="margin-bottom:0;">
    <div class="content-header">
      <span class="content-title">📋 마감일지</span>
      <div style="display:flex;gap:6px;align-items:center;">
        <span class="content-date" id="closing-date"></span>
        <button class="btn" onclick="loadPost('closing','closing-body','closing-date')" style="font-size:11px;padding:5px 10px;" title="서버에서 최신 본문 받아오기">↻ 새로고침</button>
        <button class="btn" onclick="clearClosing()" style="font-size:11px;padding:5px 10px;color:#d63031;border-color:#fab1a0;" title="마감일지 전부 비우기">🗑 초기화</button>
      </div>
    </div>
    <div class="tab-bar" id="cl-tabs">
      <button class="tab active" onclick="clTab(this,'all')">전체</button>
      <button class="tab" onclick="clTab(this,'figure')">마감수치</button>
      <button class="tab" onclick="clTab(this,'factor')">지수팩터</button>
      <button class="tab" onclick="clTab(this,'supply')">수급</button>
      <button class="tab" onclick="clTab(this,'sector')">특징업종</button>
      <button class="tab" onclick="clTab(this,'stock')">특징주</button>
      <button class="tab" onclick="clTab(this,'schedule')">내일일정</button>
    </div>
    <div class="content-body" id="closing-body"><span class="content-empty">텔레그램 봇으로 마감일지를 올리면 여기에 표시됩니다.</span></div>
    </div>
    <div class="content-card" style="margin-bottom:0;">
      <div class="content-header">
        <span class="content-title">📄 리서치 리포트</span>
        <div style="display:flex;gap:6px;">
          <label class="btn btn-primary" style="cursor:pointer;font-size:12px;padding:6px 12px;">
            + 업로드
            <input type="file" id="pdf-upload-input" accept=".pdf" multiple style="display:none;" onchange="uploadPDFs(this)"/>
          </label>
          <button class="btn" onclick="clearAllPDFs()" style="color:#d63031;border-color:#fab1a0;font-size:12px;padding:6px 12px;">↺ 전체삭제</button>
        </div>
      </div>
      <div class="tab-bar" id="pdf-tabs" style="flex-wrap:wrap;"></div>
      <div id="pdf-viewer" style="width:100%;height:500px;border-radius:8px;overflow:hidden;background:#f0f2f5;display:flex;align-items:center;justify-content:center;">
        <span class="content-empty">PDF를 업로드해주세요</span>
      </div>
    </div>
  </div>

  <!-- 주간 캘린더 -->
  <div class="section-label">📅 주간 일정 캘린더</div>
  <div class="content-card">
    <div class="content-header">
      <span class="content-title">📅 이번 주 주요 일정</span>
      <div style="display:flex;gap:6px;align-items:center;">
        <span class="saved-badge" id="cal-badge">✓ 저장됨</span>
        <button class="btn btn-primary" onclick="parseCalendar()" id="cal-btn">✦ AI 정리</button>
        <button class="btn" onclick="clearCalendar()" style="color:#d63031;border-color:#fab1a0;">↺ 초기화</button>
      </div>
    </div>
    <textarea class="input-area" id="cal-input" placeholder="주간 일정 텍스트 붙여넣기..." style="min-height:100px;"></textarea>
    <div id="cal-result" style="margin-top:14px;"></div>
  </div>

  <!-- 출연자 DB 검색 -->
  <div class="section-label">👤 출연자 DB 검색</div>
  <div class="content-card">
    <div class="content-header">
      <span class="content-title">👤 출연자 / 발언 검색</span>
      <a href="https://docs.google.com/spreadsheets/d/1PENAycUyJS0WDhvC8aXHesHxmaXTq724261aWDnsMHc/edit?gid=437534302" target="_blank" class="btn" style="font-size:12px;padding:6px 12px;">📊 DB 바로가기</a>
    </div>
    <div class="input-row">
      <input class="input-line" id="guest-input" placeholder="예) ㅇㅇㅇ / 삼성전자 / 반도체" style="flex:1;"
        onkeydown="if(event.key==='Enter')searchGuest()"/>
      <button class="btn btn-primary" onclick="searchGuest()" id="guest-btn">검색</button>
    </div>
    <div id="guest-result" style="margin-top:14px;"></div>
  </div>

  <!-- 표현 사전 -->
  <div class="section-label">📖 Encyclopedia</div>
  <div class="content-card">
    <div class="content-header">
      <span class="content-title">📖 Encyclopedia</span>
      <a href="https://docs.google.com/spreadsheets/d/1_2hIjvp2MqjHNLcv0JTn1yWgC4vB5zIzwkUS_c4cqUs/edit" target="_blank" class="btn" style="font-size:12px;padding:6px 12px;">📊 사전 바로가기</a>
    </div>
    <!-- 새 표현 추가 -->
    <div style="background:#FAFAF7;border:0.5px solid #E5E1D6;border-radius:8px;padding:10px 12px;margin-bottom:12px;">
      <div style="font-size:11px;color:#5F5E5A;font-weight:600;margin-bottom:8px;letter-spacing:0.3px;">✏️ 새 표현 추가</div>
      <div style="display:grid;grid-template-columns:120px 160px 1fr auto;gap:6px;align-items:center;">
        <input class="input-line" id="encyc-add-situation" placeholder="상황 (예: 강세)" style="font-size:12px;"
          onkeydown="if(event.key==='Enter')addEncyc()"/>
        <input class="input-line" id="encyc-add-topic" placeholder="주제 (선택)" style="font-size:12px;"
          onkeydown="if(event.key==='Enter')addEncyc()"/>
        <input class="input-line" id="encyc-add-expr" placeholder="표현 (필수) — Enter로 빠르게 추가" style="font-size:12px;"
          onkeydown="if(event.key==='Enter')addEncyc()"/>
        <button class="btn btn-primary" onclick="addEncyc()" id="encyc-add-btn" style="font-size:12px;padding:6px 14px;">＋ 추가</button>
      </div>
      <div id="encyc-add-msg" style="font-size:11px;color:#7a8099;margin-top:6px;min-height:14px;"></div>
    </div>
    <div class="input-row">
      <input class="input-line" id="encyc-input" placeholder="🔍 검색 — 예) 급락 / 금리인상 / 반도체" style="flex:1;"
        onkeydown="if(event.key==='Enter')searchEncyc()"/>
      <button class="btn btn-primary" onclick="searchEncyc()" id="encyc-btn">검색</button>
    </div>
    <div id="encyc-result" style="margin-top:14px;"></div>
  </div>

  <!-- CG DB 검색 -->
  <div class="section-label">🎬 CG DB</div>
  <div class="content-card">
    <div class="content-header">
      <span class="content-title">🎬 CG DB</span>
      <a href="https://docs.google.com/spreadsheets/d/1XbuQwCEg43OR1yAlp2YTGIo0PVx1FFzFewXftMkGd_4/edit" target="_blank" class="btn" style="font-size:12px;padding:6px 12px;">📊 시트 바로가기</a>
    </div>
    <div class="input-row">
      <input class="input-line" id="cgdb-input" placeholder="🔍 검색 — 예) 반도체 / 트럼프 이란 / CPI / 실적" style="flex:1;"
        onkeydown="if(event.key==='Enter')searchCGDB()"/>
      <button class="btn btn-primary" onclick="searchCGDB()" id="cgdb-btn">검색</button>
      <button class="btn" onclick="loadRecentCGDB()" title="최근 추가된 CG 30개" style="font-size:12px;">최근</button>
    </div>
    <div id="cgdb-result" style="margin-top:14px;"></div>
  </div>

    <div style="height:32px;"></div>
</div>
<script>
function updateClock(){
  const kst=new Date(new Date().toLocaleString('en-US',{timeZone:'Asia/Seoul'}));
  const p=n=>String(n).padStart(2,'0');
  document.getElementById('clock').textContent='KST '+p(kst.getHours())+':'+p(kst.getMinutes())+':'+p(kst.getSeconds());
}
setInterval(updateClock,1000);updateClock();

function fmt(v,d=2){
  if(v===null||v===undefined)return'—';
  return Number(v).toLocaleString('ko-KR',{minimumFractionDigits:d,maximumFractionDigits:d});
}
function cls(c){return c>0?'up':c<0?'dn':'flat';}
function sgn(c){return c>=0?'+':'';}

function renderM(iv,ic,val,chg){
  const ve=document.getElementById(iv);
  const ce=document.getElementById(ic);
  if(!ve)return;
  ve.textContent=(val!==null&&val!==undefined)?fmt(val):'—';
  if(ce&&chg!==null&&chg!==undefined)
    ce.innerHTML='<span class="'+cls(chg)+'">'+sgn(chg)+fmt(chg)+'%</span>';
}

function fmtSup(v){
  if(v===null||v===undefined||v==='')return'—';
  const n=parseInt(v);if(isNaN(n))return String(v);
  const a=Math.abs(n);
  const s=a>=1000000?(a/1000000).toFixed(1)+'M':a>=1000?(a/1000).toFixed(0)+'K':String(a);
  return(n>=0?'+':'-')+s;
}

async function loadMarket(){
  try{
    const d=await fetch('/api/market').then(r=>r.json());
    renderM('kospi-val','kospi-chg',d.kospi?.value,parseFloat(d.kospi?.change));
    renderM('kosdaq-val','kosdaq-chg',d.kosdaq?.value,parseFloat(d.kosdaq?.change));
    renderM('nasdaq-val','nasdaq-chg',d.nasdaq?.value,d.nasdaq?.change);
    renderM('dow-val','dow-chg',d.dow?.value,d.dow?.change);
    renderM('sp500-val','sp500-chg',d.sp500?.value,d.sp500?.change);
    renderM('ewy-val','ewy-chg',d.ewy?.value,d.ewy?.change);
    renderM('wti-val','wti-chg',d.wti?.value,d.wti?.change);
    renderM('brent-val','brent-chg',d.brent?.value,d.brent?.change);
    renderM('vix-val','vix-chg',d.vix?.value,d.vix?.change);
    renderM('sox-val','sox-chg',d.sox?.value,d.sox?.change);
    const tnxEl=document.getElementById('tnx-val');
    const tnxChgEl=document.getElementById('tnx-chg');
    if(tnxEl&&d.tnx?.value!==null){
      tnxEl.textContent=fmt(d.tnx?.value)+'%';
      if(tnxChgEl&&d.tnx?.change!==null)
        tnxChgEl.innerHTML='<span class="'+cls(d.tnx.change)+'">'+sgn(d.tnx.change)+fmt(d.tnx.change)+'%p</span>';
    }
    renderM('usdkrw-val','usdkrw-chg',d.usdkrw?.value,d.usdkrw?.change);
    renderM('gold-val','gold-chg',d.gold?.value,d.gold?.change);
    renderM('dxy-val','dxy-chg',d.dxy?.value,d.dxy?.change);
    renderM('dram-val','dram-chg',d.dram?.value,d.dram?.change);
    ['kospi','kosdaq'].forEach(m=>{
      const el=document.getElementById(m+'-sup');if(!el)return;
      const fv=d[m]?.foreign,iv=d[m]?.institution;
      const fvn=parseInt(fv),ivn=parseInt(iv);
      const indv=d[m]?.individual,indvn=parseInt(indv);
      el.innerHTML='<span>개인 <b class="'+(indvn>0?'up':indvn<0?'dn':'flat')+'">'+fmtSup(indv)+'</b></span>'+
                   '<span>외인 <b class="'+(fvn>0?'up':fvn<0?'dn':'flat')+'">'+fmtSup(fv)+'</b></span>'+
                   '<span>기관 <b class="'+(ivn>0?'up':ivn<0?'dn':'flat')+'">'+fmtSup(iv)+'</b></span>';
    });
  }catch(e){console.error(e);}
}

async function loadSector(){
  try{
    const d=await fetch('/api/sector').then(r=>r.json());
    const render=(id,arr)=>{
      const el=document.getElementById(id);if(!el)return;
      if(!arr||!arr.length){el.innerHTML='<span class="content-empty">데이터 없음</span>';return;}
      el.innerHTML=arr.map(e=>'<div class="etf-row"><span class="etf-name">'+e.name+'</span>'+
        '<span class="etf-chg '+cls(e.change)+'">'+sgn(e.change)+fmt(e.change)+'%</span></div>').join('');
    };
    render('sector-top',d.top5);render('sector-bot',d.bot5);
  }catch(e){}
}

async function loadPost(type,bid,did){
  try{
    const d=await fetch('/api/post/'+type).then(r=>r.json());
    if(d.content){
      const b=document.getElementById(bid),dt=document.getElementById(did);
      if(dt)dt.textContent=d.date||'';
      if(b){
        let activeTabKey = null;
        if(type==='checkpoint'){
          const activeTab = document.querySelector('#cp-tabs .tab.active');
          if(activeTab){
            const m = activeTab.getAttribute('onclick')?.match(/cpTab\(this,'(\w+)'\)/);
            if(m) activeTabKey = m[1];
          }
        } else if(type==='closing'){
          const activeTab = document.querySelector('#cl-tabs .tab.active');
          if(activeTab){
            const m = activeTab.getAttribute('onclick')?.match(/clTab\(this,'(\w+)'\)/);
            if(m) activeTabKey = m[1];
          }
        }
        if(type==='checkpoint') _cpRaw=d.content;
        if(type==='closing') _clRaw=d.content;
        let restoredKey = activeTabKey || 'all';
        if(type==='checkpoint'){
          const targetBtn = document.querySelector('#cp-tabs .tab[onclick*="\''+restoredKey+'\'"]') 
            || document.querySelector('#cp-tabs .tab.active')
            || document.querySelector('#cp-tabs .tab');
          if(targetBtn) cpTab(targetBtn, restoredKey);
          else b.textContent = d.content;
        } else if(type==='closing'){
          const targetBtn = document.querySelector('#cl-tabs .tab[onclick*="\''+restoredKey+'\'"]') 
            || document.querySelector('#cl-tabs .tab.active')
            || document.querySelector('#cl-tabs .tab');
          if(targetBtn) clTab(targetBtn, restoredKey);
          else b.textContent = d.content;
        } else {
          b.textContent = d.content;
        }
      }
    }
  }catch(e){}
}

async function loadFutures(){
  try{
    const d=await fetch('/api/post/futures').then(r=>r.json());
    if(d.content){
      const el=document.getElementById('futures-display');
      if(el)el.textContent=d.content;
      const inp=document.getElementById('futures-input');
      if(inp)inp.value=d.content;
    }
  }catch(e){}
}

async function loadAutoFutures(){
  try{
    const d=await fetch('/api/market').then(r=>r.json());
    if(d.futures_auto){
      const el=document.getElementById('futures-display');
      const inp=document.getElementById('futures-input');
      if(el && (!el.textContent || el.textContent==='—')) {
        el.textContent=d.futures_auto;
      }
      const autoEl=document.getElementById('futures-auto-val');
      if(autoEl) autoEl.textContent='KIS: '+d.futures_auto;
    }
  }catch(e){}
}

async function clearFutures(){
  await fetch('/api/post/futures',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({content:'',date:new Date().toISOString().slice(0,10)})});
  document.getElementById('futures-display').textContent='—';
  document.getElementById('futures-input').value='';
}

async function saveFutures(){
  const val=document.getElementById('futures-input').value.trim();
  if(!val)return;
  await fetch('/api/post/futures',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({content:val,date:new Date().toISOString().slice(0,10)})
  });
  document.getElementById('futures-display').textContent=val;
  const badge=document.getElementById('futures-badge');
  badge.style.display='inline';
  setTimeout(()=>badge.style.display='none',2000);
}

let _reportTab = 'up';

function reportTab(btn, key){
  document.querySelectorAll('#rtab-up,#rtab-dn,#rtab-feature').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  _reportTab = key;
  ['up','dn','feature'].forEach(k=>{
    const el=document.getElementById('report-'+k+'-input');
    if(el) el.style.display = k===key?'block':'none';
  });
}

async function saveReport(){
  const val=document.getElementById('report-input')?.value.trim();
  if(val===undefined) return;
  await fetch('/api/post/report',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({content:val,date:new Date().toISOString().slice(0,10)})});
  const badge=document.getElementById('report-badge');
  badge.style.display='inline';setTimeout(()=>badge.style.display='none',2000);
}

async function clearReport(){
  if(!confirm('리포트 전체 초기화할까요?')) return;
  await fetch('/api/report/clear',{method:'POST'});
  ['up','dn','feature'].forEach(k=>{
    const el=document.getElementById('report-'+k+'-input');
    if(el) el.value='';
  });
}

async function loadReport(){
  try{
    const d=await fetch('/api/post/report').then(r=>r.json());
    if(d.content){
      const el=document.getElementById('report-input');
      if(el) el.value=d.content;
    }
  }catch(e){}
}

async function searchKstock(){
  const q=document.getElementById('kstock-input').value.trim();
  if(!q)return;
  const btn=document.getElementById('kstock-btn');
  const result=document.getElementById('kstock-result');
  const newsDiv=document.getElementById('kstock-news');
  btn.classList.add('ls');btn.innerHTML='<span class="spinner"></span>';
  result.innerHTML='<span class="content-empty">검색 중...</span>';
  if(newsDiv) newsDiv.innerHTML='';
  try{
    const d=await fetch('/api/kstock/search?q='+encodeURIComponent(q)).then(r=>r.json());
    if(d.error){
      result.innerHTML='<span class="content-empty">오류: '+d.error+'</span>';
      btn.classList.remove('ls');btn.innerHTML='검색';
      return;
    }
    if(!d.found){
      result.innerHTML='<span class="content-empty">❓ \''+q+'\' — 등록된 종목/테마가 없어요.</span>';
      btn.classList.remove('ls');btn.innerHTML='검색';
      return;
    }

    let html='';

    (d.stock_cards||[]).forEach(card=>{
      html += '<div style="padding:14px 16px;background:#f8f9fa;border-radius:10px;margin-bottom:10px;border-left:3px solid #e8b84b;">';
      html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px;">';
      html += '<span style="font-size:15px;font-weight:700;color:#1a1d23;">📌 '+card.name+'</span>';
      if(card.code){
        html += '<a href="'+card.naver_url+'" target="_blank" style="font-size:11px;color:#7a8099;font-family:monospace;text-decoration:none;background:#fff;padding:2px 8px;border-radius:4px;border:1px solid #dfe6e9;">'+card.code+'</a>';
      }
      html += '</div>';
      (card.themes||[]).forEach(t=>{
        html += '<div style="margin-top:8px;padding-top:8px;border-top:1px dashed #dfe6e9;">';
        html += '<div style="display:inline-block;font-size:11px;font-weight:700;color:#fff;background:#1a1d23;padding:3px 9px;border-radius:5px;margin-bottom:5px;">'+t.theme+'</div>';
        if(t.desc) html += '<div style="font-size:12.5px;color:#2d3436;line-height:1.55;">'+t.desc+'</div>';
        html += '</div>';
      });
      html += '</div>';
    });

    if(d.theme_cards && d.theme_cards.length){
      html += '<div style="font-size:12px;font-weight:700;color:#7a8099;letter-spacing:.05em;margin:6px 0 10px;padding-bottom:6px;border-bottom:1px solid #dfe6e9;">🗂 \''+q+'\' 관련 종목 · '+d.theme_cards.length+'건</div>';
      d.theme_cards.forEach((card,i)=>{
        html += '<div style="padding:11px 14px;background:#f8f9fa;border-radius:10px;margin-bottom:7px;border-left:3px solid #e8b84b;">';
        html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;gap:8px;">';
        html += '<span style="font-size:13px;font-weight:700;color:#1a1d23;">'+(i+1)+'. '+card.name+'</span>';
        if(card.code){
          html += '<a href="https://m.stock.naver.com/domestic/stock/'+card.code+'/total" target="_blank" style="font-size:11px;color:#7a8099;font-family:monospace;text-decoration:none;background:#fff;padding:2px 7px;border-radius:4px;border:1px solid #dfe6e9;">'+card.code+'</a>';
        }
        html += '</div>';
        if(card.theme){
          html += '<div style="display:inline-block;font-size:10px;font-weight:700;color:#fff;background:#636e72;padding:2px 7px;border-radius:4px;margin-bottom:4px;">'+card.theme+'</div>';
        }
        if(card.desc){
          html += '<div style="font-size:12px;color:#2d3436;line-height:1.5;margin-top:3px;">'+card.desc+'</div>';
        }
        html += '</div>';
      });
    }

    result.innerHTML = html || '<span class="content-empty">결과 없음</span>';

    let sideHtml='';
    (d.stock_cards||[]).forEach(card=>{
      if(!card.price_info) return;
      const p=card.price_info;
      const accent = p.up ? '#d63031' : '#0984e3';
      sideHtml += '<div style="padding:18px 20px;background:#f8f9fa;border-radius:12px;margin-bottom:12px;border-left:3px solid '+accent+';">';
      sideHtml += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px;">';
      sideHtml += '<span style="font-size:11px;font-weight:700;color:#7a8099;letter-spacing:.08em;">📊 현재가</span>';
      sideHtml += '<span style="font-size:11px;color:#7a8099;font-family:monospace;">'+card.name+(card.code?' · '+card.code:'')+'</span>';
      sideHtml += '</div>';
      sideHtml += '<div style="font-size:30px;font-weight:700;color:#1a1d23;letter-spacing:-0.5px;line-height:1.1;">'+p.price+'<span style="font-size:16px;font-weight:500;color:#7a8099;margin-left:2px;">원</span></div>';
      sideHtml += '<div style="margin-top:6px;font-size:14px;font-weight:600;color:'+accent+';">'+(p.up?'▲':'▼')+' '+p.change+' ('+p.pct+'%)</div>';
      if(card.chart_url){
        sideHtml += '<a href="'+card.chart_url+'" target="_blank" style="display:inline-block;margin-top:12px;font-size:12px;color:#0984e3;font-weight:600;text-decoration:none;background:#fff;padding:6px 12px;border-radius:6px;border:1px solid #dfe6e9;">📈 네이버 차트 →</a>';
      }
      sideHtml += '</div>';
    });

    const hasNews = (d.news_latest && d.news_latest.length) || (d.news_relevant && d.news_relevant.length);
    if(hasNews){
      sideHtml += '<div style="font-size:12px;font-weight:700;color:#7a8099;letter-spacing:.05em;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #dfe6e9;">📰 \''+q+'\' 관련 뉴스</div>';
      if(d.news_latest && d.news_latest.length){
        sideHtml += '<div style="font-size:11px;font-weight:700;color:#e8b84b;margin-bottom:6px;">🕐 최신순</div>';
        d.news_latest.forEach((n,i)=>{
          sideHtml += '<a href="'+n.link+'" target="_blank" style="display:block;padding:9px 11px;background:#f8f9fa;border-radius:8px;margin-bottom:5px;text-decoration:none;border-left:2px solid #e8b84b;">';
          sideHtml += '<div style="font-size:12.5px;font-weight:600;color:#1a1d23;line-height:1.4;">'+(i+1)+'. '+n.title+'</div>';
          sideHtml += '</a>';
        });
      }
      if(d.news_relevant && d.news_relevant.length){
        sideHtml += '<div style="font-size:11px;font-weight:700;color:#636e72;margin-top:14px;margin-bottom:6px;">🎯 관련도순</div>';
        d.news_relevant.forEach((n,i)=>{
          sideHtml += '<a href="'+n.link+'" target="_blank" style="display:block;padding:9px 11px;background:#f8f9fa;border-radius:8px;margin-bottom:5px;text-decoration:none;border-left:2px solid #636e72;">';
          sideHtml += '<div style="font-size:12.5px;font-weight:600;color:#1a1d23;line-height:1.4;">'+(i+1)+'. '+n.title+'</div>';
          sideHtml += '</a>';
        });
      }
    }

    if(newsDiv) newsDiv.innerHTML = sideHtml || '<span class="content-empty">관련 정보 없음</span>';

  }catch(e){result.innerHTML='<span class="content-empty">네트워크 오류</span>';}
  btn.classList.remove('ls');btn.innerHTML='검색';
}

let _todos = [];

function renderTodos(){
  const list = document.getElementById('todo-list');
  if(!list) return;
  if(!_todos.length){
    list.innerHTML='<div style="color:#b2bec3;font-size:13px;padding:4px 0;">할일이 없어요 😊</div>';
    return;
  }
  list.innerHTML = _todos.map((t,i)=>
    '<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid #f0f2f5;">'+
    '<input type="checkbox" '+(t.done?'checked':'')+' onchange="toggleTodo('+i+')" style="width:16px;height:16px;cursor:pointer;accent-color:#00b894;"/>'+
    '<span style="flex:1;font-size:13px;'+(t.done?'text-decoration:line-through;color:#b2bec3;':'')+'" onclick="editTodo('+i+')" >'+t.text+'</span>'+
    '<button onclick="deleteTodo('+i+')" style="border:none;background:none;color:#dfe6e9;cursor:pointer;font-size:14px;">✕</button>'+
    '</div>'
  ).join('');
}

function toggleTodo(i){
  _todos[i].done = !_todos[i].done;
  saveTodoDB();
  renderTodos();
}

function deleteTodo(i){
  _todos.splice(i,1);
  saveTodoDB();
  renderTodos();
}

function editTodo(i){
  const newText = prompt('수정:', _todos[i].text);
  if(newText && newText.trim()){
    _todos[i].text = newText.trim();
    saveTodoDB();
    renderTodos();
  }
}

function addTodoItem(){
  const inp = document.getElementById('todo-new-input');
  const val = inp?.value.trim();
  if(!val) return;
  _todos.push({text:val, done:false});
  inp.value='';
  saveTodoDB();
  renderTodos();
}

async function saveTodoDB(){
  await fetch('/api/post/todo',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({content:JSON.stringify(_todos),date:new Date().toISOString().slice(0,10)})});
  const badge=document.getElementById('todo-badge');
  if(badge){badge.style.display='inline';setTimeout(()=>badge.style.display='none',1500);}
}

async function loadTodo(){
  try{
    const d=await fetch('/api/post/todo').then(r=>r.json());
    if(d.content){
      try{ _todos=JSON.parse(d.content); }catch(e){ _todos=[]; }
    }
    renderTodos();
  }catch(e){ renderTodos(); }
}

async function saveMemo(){
  const val=document.getElementById('memo-input').value.trim();
  await fetch('/api/post/memo',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({content:val,date:new Date().toISOString().slice(0,10)})});
  const badge=document.getElementById('memo-badge');
  badge.style.display='inline';setTimeout(()=>badge.style.display='none',2000);
}
async function clearMemo(){
  if(!confirm('메모 초기화할까요?'))return;
  await fetch('/api/memo/clear',{method:'POST'});
  document.getElementById('memo-input').value='';
}
async function loadMemo(){
  try{
    const d=await fetch('/api/post/memo').then(r=>r.json());
    if(d.content) document.getElementById('memo-input').value=d.content;
  }catch(e){}
}
const _origFetch = window.fetch;
window.fetch = function(url, options){
  options = options || {};
  if(typeof url === 'string' && (url.startsWith('/') || url.startsWith(location.origin))){
    options.credentials = options.credentials || 'include';
    options.headers = options.headers || {};
    if(window._API_SECRET){
      if(options.headers instanceof Headers){
        options.headers.set('X-API-Secret', window._API_SECRET);
      } else {
        options.headers['X-API-Secret'] = window._API_SECRET;
      }
    }
  }
  return _origFetch(url, options);
};

function rtGetTarget(btn){
  const tb = btn.closest('.richtext-toolbar');
  return document.getElementById(tb.dataset.target);
}
function rtCmd(btn, cmd){
  const el = rtGetTarget(btn);
  el.focus();
  document.execCommand(cmd, false, null);
}
function rtHighlight(btn, color){
  const el = rtGetTarget(btn);
  el.focus();
  document.execCommand('backColor', false, color);
}
function rtColor(btn, color){
  const el = rtGetTarget(btn);
  el.focus();
  document.execCommand('foreColor', false, color);
}
function rtSize(btn, size){
  const el = rtGetTarget(btn);
  el.focus();
  document.execCommand('fontSize', false, size);
}
function rtBox(btn, color){
  const el = rtGetTarget(btn);
  el.focus();
  const sel = window.getSelection();
  if(!sel.rangeCount) return;
  const range = sel.getRangeAt(0);
  let inner;
  if(range.collapsed){
    inner = '내용 입력...';
  } else {
    const tmp = document.createElement('div');
    tmp.appendChild(range.cloneContents());
    inner = tmp.innerHTML;
  }
  const styles = {
    pink: 'background:#FBEAF0;color:#4B1528;',
    blue: 'background:#E6F1FB;color:#0C447C;'
  };
  const style = styles[color] || styles.pink;
  const html = '<div style="' + style + 'padding:8px 12px;border-radius:7px;margin:6px 0;">' + inner + '</div><br>';
  document.execCommand('insertHTML', false, html);
}

document.addEventListener('keydown', function(e){
  const active = document.activeElement;
  if(!active) return;
  if(!active.classList.contains('rich-editor') && !active.classList.contains('q-body')) return;
  const mod = e.metaKey || e.ctrlKey;
  if(!mod || !e.shiftKey) return;
  const key = e.key.toLowerCase();
  if(key === 'h'){
    e.preventDefault();
    document.execCommand('backColor', false, '#fff3a0');
  }
  else if(key === 'l'){
    e.preventDefault();
    document.execCommand('fontSize', false, '5');
  }
  else if(key === 'b'){
    e.preventDefault();
    document.execCommand('foreColor', false, '#0984e3');
  }
  else if(key === 'g'){
    e.preventDefault();
    document.execCommand('foreColor', false, '#00b894');
  }
  else if(key === 'k'){
    e.preventDefault();
    const sel = window.getSelection();
    if(!sel.rangeCount) return;
    const range = sel.getRangeAt(0);
    let inner;
    if(range.collapsed){
      inner = '내용 입력...';
    } else {
      const tmp = document.createElement('div');
      tmp.appendChild(range.cloneContents());
      inner = tmp.innerHTML;
    }
    const html = '<div style="background:#FBEAF0;color:#4B1528;padding:8px 12px;border-radius:7px;margin:6px 0;">' + inner + '</div><br>';
    document.execCommand('insertHTML', false, html);
  }
  else if(key === 'e'){
    e.preventDefault();
    const sel = window.getSelection();
    if(!sel.rangeCount) return;
    const range = sel.getRangeAt(0);
    let inner;
    if(range.collapsed){
      inner = '내용 입력...';
    } else {
      const tmp = document.createElement('div');
      tmp.appendChild(range.cloneContents());
      inner = tmp.innerHTML;
    }
    const html = '<div style="background:#E6F1FB;color:#0C447C;padding:8px 12px;border-radius:7px;margin:6px 0;">' + inner + '</div><br>';
    document.execCommand('insertHTML', false, html);
  }
}, true);

// === 노트 (멀티 탭) ===
// 데이터 모델: {tabs:[{id,name,content}], activeId}
// 백엔드에는 JSON 문자열로 저장. 옛 단일 텍스트 노트는 자동 마이그레이션.
let _noteData = {tabs:[], activeId:null};

function _newNoteId(){ return 't'+Date.now()+Math.random().toString(36).slice(2,5); }

function parseNoteData(raw){
  if(!raw) return {tabs:[{id:_newNoteId(),name:'기본',content:''}], activeId:null};
  // JSON 시도
  if(typeof raw === 'string' && raw.trim().startsWith('{')){
    try{
      const j = JSON.parse(raw);
      if(j && Array.isArray(j.tabs) && j.tabs.length){
        if(!j.activeId || !j.tabs.find(t=>t.id===j.activeId)) j.activeId = j.tabs[0].id;
        return j;
      }
    }catch(e){}
  }
  // 옛 포맷 (단일 HTML) → 첫 탭으로 마이그레이션
  const id = _newNoteId();
  return {tabs:[{id:id,name:'기본',content:String(raw)}], activeId:id};
}

function renderNoteTabs(){
  const bar = document.getElementById('note-tab-bar');
  if(!bar) return;
  let html = '';
  _noteData.tabs.forEach(t => {
    const active = t.id === _noteData.activeId ? ' active' : '';
    const safeName = (t.name||'노트').replace(/[<>&"']/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c]));
    html += '<span class="note-tab'+active+'" data-tid="'+t.id+'" onclick="switchNoteTab(\''+t.id+'\')" ondblclick="renameNoteTab(\''+t.id+'\')" title="더블클릭으로 이름변경">'+
      safeName+
      '<span class="close-x" onclick="event.stopPropagation();deleteNoteTab(\''+t.id+'\')" title="삭제">✕</span>'+
      '</span>';
  });
  html += '<button class="note-tab-add" onclick="addNoteTab()" title="새 탭 추가">＋</button>';
  bar.innerHTML = html;
}

function _syncCurrentEditorToData(){
  const el = document.getElementById('note-rich');
  if(!el || !_noteData.activeId) return;
  const tab = _noteData.tabs.find(t => t.id === _noteData.activeId);
  if(tab) tab.content = el.innerHTML;
}

function _loadActiveTabToEditor(){
  const el = document.getElementById('note-rich');
  if(!el) return;
  const tab = _noteData.tabs.find(t => t.id === _noteData.activeId);
  el.innerHTML = tab ? (tab.content||'') : '';
}

window.switchNoteTab = function(id){
  if(id === _noteData.activeId) return;
  _syncCurrentEditorToData();
  _noteData.activeId = id;
  renderNoteTabs();
  _loadActiveTabToEditor();
};

window.addNoteTab = function(){
  _syncCurrentEditorToData();
  const name = prompt('새 탭 이름', '노트'+(_noteData.tabs.length+1));
  if(name === null) return;
  const id = _newNoteId();
  _noteData.tabs.push({id:id, name:(name.trim()||'노트'+(_noteData.tabs.length+1)), content:''});
  _noteData.activeId = id;
  renderNoteTabs();
  _loadActiveTabToEditor();
  saveNote();
};

window.deleteNoteTab = function(id){
  if(_noteData.tabs.length <= 1){
    alert('탭은 최소 1개는 있어야 해요. 내용을 비우려면 ↺ 초기화를 사용하세요.');
    return;
  }
  const t = _noteData.tabs.find(x=>x.id===id);
  if(!t) return;
  if(!confirm('"'+t.name+'" 탭을 삭제할까요? 안의 내용도 같이 사라져요.')) return;
  _noteData.tabs = _noteData.tabs.filter(x=>x.id!==id);
  if(_noteData.activeId === id) _noteData.activeId = _noteData.tabs[0].id;
  renderNoteTabs();
  _loadActiveTabToEditor();
  saveNote();
};

window.renameNoteTab = function(id){
  const t = _noteData.tabs.find(x=>x.id===id);
  if(!t) return;
  const name = prompt('탭 이름 변경', t.name);
  if(name === null) return;
  t.name = name.trim() || t.name;
  renderNoteTabs();
  saveNote();
};

async function saveNote(){
  _syncCurrentEditorToData();
  const payload = JSON.stringify(_noteData);
  await fetch('/api/post/note',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({content:payload,date:new Date().toISOString().slice(0,10)})});
  const badge=document.getElementById('note-badge');
  if(badge){ badge.style.display='inline'; setTimeout(()=>badge.style.display='none',2000); }
}

async function clearNote(){
  const tab = _noteData.tabs.find(t => t.id === _noteData.activeId);
  if(!confirm('"'+(tab?tab.name:'현재')+'" 탭 내용을 초기화할까요? (다른 탭은 그대로)')) return;
  if(tab) tab.content = '';
  const el = document.getElementById('note-rich');
  if(el) el.innerHTML = '';
  saveNote();
}

async function loadNote(){
  try{
    const d = await fetch('/api/post/note').then(r=>r.json());
    _noteData = parseNoteData(d ? d.content : '');
    if(!_noteData.activeId && _noteData.tabs.length) _noteData.activeId = _noteData.tabs[0].id;
    renderNoteTabs();
    _loadActiveTabToEditor();
  }catch(e){
    _noteData = parseNoteData('');
    renderNoteTabs();
    _loadActiveTabToEditor();
  }
}
let _calData = null;

async function saveCalendarDB(){
  if(!_calData) return;
  await fetch('/api/calendar/update',{
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({data: _calData})
  });
}

function renderCalendar(data){
  _calData = data;
  const result = document.getElementById('cal-result');
  if(!data || !data.days || !data.days.length){
    result.innerHTML='<span class="content-empty">일정이 없어요.</span>';
    return;
  }
  const weekLabel = data.week_label || '';
  let html = '';
  if(weekLabel){
    html += '<div style="font-size:12px;font-weight:700;color:#7a8099;margin-bottom:10px;">' + weekLabel + '</div>';
  }

  html += '<div class="tab-bar" id="cal-tabs">';
  data.days.forEach(function(day, i){
    const active = i===0 ? 'active' : '';
    const shortDate = day.date.match(/\d+일/) ? day.date.match(/\d+일/)[0] : day.date.slice(0,6);
    const dayName = day.date.match(/[월화수목금토일]요일/) ? day.date.match(/[월화수목금토일]요일/)[0] : '';
    html += '<button class="tab ' + active + '" onclick="calTab(this,\'cal-day-' + i + '\')">' + shortDate + ' ' + dayName + '</button>';
  });
  html += '</div>';

  data.days.forEach(function(day, i){
    const display = i===0 ? 'block' : 'none';
    html += '<div id="cal-day-' + i + '" style="display:' + display + ';">';
    html += '<div style="font-size:13px;font-weight:700;color:#2d3436;margin-bottom:8px;">' + day.date + '</div>';

    html += '<div id="cal-items-' + i + '">';
    (day.items||[]).forEach(function(item,j){
      const safeItem = item.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
      html += '<div style="padding:6px 0;border-bottom:1px solid #f0f2f5;display:flex;align-items:flex-start;gap:6px;">';
      html += '<span style="color:#e8b84b;font-weight:700;font-size:13px;min-width:18px;">' + (j+1) + '.</span>';
      html += '<input value="' + safeItem + '" style="flex:1;border:none;background:transparent;font-size:13px;color:#2d3436;padding:0;" onchange="_calData.days[' + i + '].items[' + j + ']=this.value;saveCalendarDB()"/>';
      html += '<button onclick="deleteCalItem(' + i + ',' + j + ')" style="border:none;background:none;color:#b2bec3;cursor:pointer;font-size:14px;padding:0 2px;">✕</button>';
      html += '</div>';
    });
    html += '</div>';

    html += '<div style="margin-top:8px;">';
    html += '<input id="cal-new-' + i + '" class="input-area" placeholder="+ 일정 추가..." style="min-height:auto;padding:6px 10px;font-size:13px;" onkeydown="if(event.key===\'Enter\'){addCalItem(' + i + ');event.preventDefault();}"/>';
    html += '<button class="btn" onclick="addCalItem(' + i + ')" style="margin-top:4px;font-size:12px;padding:5px 10px;">+ 추가</button>';
    html += '</div>';

    html += '<div style="margin-top:12px;border-top:1px solid #f0f2f5;padding-top:10px;">';
    html += '<div style="font-size:11px;font-weight:700;color:#7a8099;margin-bottom:6px;">👤 출연자</div>';
    html += '<div id="cal-guests-' + i + '">';
    (day.guests||[]).forEach(function(guest,j){
      const safeGuest = guest.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
      html += '<div style="padding:5px 0;border-bottom:1px solid #f0f2f5;display:flex;align-items:center;gap:6px;">';
      html += '<span style="color:#0984e3;font-weight:700;font-size:12px;min-width:18px;">' + (j+1) + '.</span>';
      html += '<input value="' + safeGuest + '" style="flex:1;border:none;background:transparent;font-size:13px;color:#2d3436;padding:0;" onchange="_calData.days[' + i + '].guests[' + j + ']=this.value;saveCalendarDB()"/>';
      html += '<button onclick="deleteCalGuest(' + i + ',' + j + ')" style="border:none;background:none;color:#b2bec3;cursor:pointer;font-size:14px;padding:0 2px;">✕</button>';
      html += '</div>';
    });
    html += '</div>';
    html += '<div style="margin-top:6px;display:flex;gap:6px;">';
    html += '<input id="cal-new-guest-' + i + '" class="input-area" placeholder="+ 출연자 추가... (이름 - 주제)" style="min-height:auto;padding:6px 10px;font-size:13px;flex:1;" onkeydown="if(event.key===\'Enter\'){addCalGuest(' + i + ');event.preventDefault();}"/>';
    html += '<button class="btn" onclick="addCalGuest(' + i + ')" style="font-size:12px;padding:5px 10px;">+ 추가</button>';
    html += '</div>';
    html += '</div>';

    html += '</div>';
  });

  result.innerHTML = html;
}

function deleteCalGuest(dayIdx, guestIdx){
  if(!_calData) return;
  _calData.days[dayIdx].guests = _calData.days[dayIdx].guests || [];
  _calData.days[dayIdx].guests.splice(guestIdx, 1);
  saveCalendarDB();
  renderCalendar(_calData);
  const tabs = document.querySelectorAll('#cal-tabs .tab');
  if(tabs[dayIdx]) calTab(tabs[dayIdx], 'cal-day-'+dayIdx);
}

function addCalGuest(dayIdx){
  const inp = document.getElementById('cal-new-guest-'+dayIdx);
  const val = inp?.value.trim();
  if(!val || !_calData) return;
  _calData.days[dayIdx].guests = _calData.days[dayIdx].guests || [];
  _calData.days[dayIdx].guests.push(val);
  saveCalendarDB();
  renderCalendar(_calData);
  const tabs = document.querySelectorAll('#cal-tabs .tab');
  if(tabs[dayIdx]) calTab(tabs[dayIdx], 'cal-day-'+dayIdx);
}

function addCalItem(dayIdx){
  const inp = document.getElementById('cal-new-'+dayIdx);
  const val = inp?.value.trim();
  if(!val || !_calData) return;
  _calData.days[dayIdx].items = _calData.days[dayIdx].items || [];
  _calData.days[dayIdx].items.push(val);
  saveCalendarDB();
  renderCalendar(_calData);
  const tabs = document.querySelectorAll('#cal-tabs .tab');
  if(tabs[dayIdx]) calTab(tabs[dayIdx], 'cal-day-'+dayIdx);
}

function deleteCalItem(dayIdx, itemIdx){
  if(!_calData || !_calData.days[dayIdx]) return;
  _calData.days[dayIdx].items.splice(itemIdx, 1);
  saveCalendarDB();
  renderCalendar(_calData);
  const tabs = document.querySelectorAll('#cal-tabs .tab');
  if(tabs[dayIdx]) calTab(tabs[dayIdx], 'cal-day-'+dayIdx);
}

function calTab(btn, panelId){
  document.querySelectorAll('#cal-tabs .tab').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('#cal-result [id^="cal-day-"]').forEach(el=>el.style.display='none');
  const el=document.getElementById(panelId);
  if(el)el.style.display='block';
}

async function parseCalendar(){
  const raw=document.getElementById('cal-input').value.trim();
  if(!raw)return;
  const btn=document.getElementById('cal-btn');
  btn.classList.add('ls');btn.innerHTML='<span class="spinner"></span> AI 정리 중...';
  try{
    const d=await fetch('/api/calendar/parse',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({content:raw})
    }).then(r=>r.json());
    if(d.error){
      document.getElementById('cal-result').innerHTML='<span class="content-empty">오류: '+d.error+'</span>';
    } else {
      if(_calData && _calData.days){
        d.days.forEach(newDay => {
          const existing = _calData.days.find(d2 => d2.day_key === newDay.day_key);
          if(existing){
            existing.items = [...(existing.items||[]), ...(newDay.items||[])];
          }
        });
        renderCalendar(_calData);
      } else {
        renderCalendar(d);
      }
      document.getElementById('cal-input').value='';
      const badge=document.getElementById('cal-badge');
      badge.style.display='inline';setTimeout(()=>badge.style.display='none',2000);
    }
  }catch(e){document.getElementById('cal-result').innerHTML='<span class="content-empty">오류 발생</span>';}
  btn.classList.remove('ls');btn.innerHTML='✦ AI 정리';
}

async function clearCalendar(){
  if(!confirm('캘린더 초기화할까요?'))return;
  await fetch('/api/calendar/clear',{method:'POST'});
  _calData = null;
  document.getElementById('cal-input').value='';
  document.getElementById('cal-result').innerHTML='';
}

let _wdbActiveTab = 0;

function parseWdb(text){
  if(!text||!text.trim()) return [];
  const lines = text.split('\n');
  const corners = [];
  let current = null;
  for(const line of lines){
    const match = line.match(/^\s*#(\d+)\s*\.?\s*(.*)/);
    if(match){
      if(current) corners.push(current);
      current = {number:match[1], title:match[2].trim()||'코너'+match[1], body:[], guests:[]};
    } else if(current){
      current.body.push(line);
    } else if(line.trim()){
      if(!corners.length) corners.push({number:'0', title:'인트로', body:[line], guests:[]});
      else corners[0].body.push(line);
    }
  }
  if(current) corners.push(current);
  
  corners.forEach(c=>{
    const seen = new Set();
    c.guests = [];
    const searchLines = [c.title, ...(c.body || [])];
    for(const line of searchLines){
      const re = /([가-힣]{2,4})\s*[|｜]\s*([^|｜\n]+?)(?=(?:\s*[+＋]|\s*,|\s*\/|\s*$|\s{2,}))/g;
      let m;
      while((m = re.exec(line)) !== null){
        const name = m[1].trim();
        let title = m[2].trim();
        title = title.replace(/[—–\-]+$/, '').replace(/^[—–\-]+/, '').trim();
        title = title.replace(/\s*\(([^)]+)\).*$/, ' ($1)');
        if(!title) continue;
        const key = name + '|' + title;
        if(!seen.has(key)){
          seen.add(key);
          c.guests.push({name, title});
        }
      }
      if(!c.guests.length){
        const simple = line.match(/([가-힣]{2,4})\s*[|｜]\s*(.+?)(?:\s*[—–\-(]|\s*$)/);
        if(simple){
          const name = simple[1].trim();
          const title = simple[2].trim();
          const key = name + '|' + title;
          if(title && !seen.has(key)){
            seen.add(key);
            c.guests.push({name, title});
          }
        }
      }
    }
    c.guests = c.guests.slice(0, 4);
  });
  return corners;
}

function escapeHtml(s){
  return (s==null?'':String(s)).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function escapeAttr(s){
  return (s==null?'':String(s)).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function hasQPattern(bodyLines){
  return (bodyLines||[]).some(l=>/^\s*Q[\d-]+\s*\.\s*/.test(l));
}
function getQTypeClass(type){
  if(!type) return 'qt-2dan';
  if(type.indexOf('신뢰형')>=0 || type.indexOf('🅰')>=0) return 'qt-trust';
  if(type.indexOf('연결형')>=0 || type.indexOf('🅱')>=0) return 'qt-connect';
  if(type.indexOf('임팩트형')>=0 || type.indexOf('🅵')>=0) return 'qt-impact';
  return 'qt-2dan';
}
function parseQuestions(bodyLines){
  const qs = [];
  let current = null;
  const qHeaderRe = /^\s*Q[\d-]+\s*\.\s*/;
  for(const line of (bodyLines||[])){
    if(qHeaderRe.test(line)){
      if(current) qs.push(current);
      current = {header: line.trim(), qNum:'', qType:'', qTitle:'', qGuest:'', body:[]};
      const m = line.trim().match(/^(Q[\d-]+)\s*\.\s*(?:\[([^\]]+?)\])?\s*([^\[]+?)(?:\s*\[([^\]]+?)\])?\s*$/);
      if(m){
        current.qNum = (m[1]||'').trim();
        current.qType = (m[2]||'').trim();
        current.qTitle = (m[3]||'').trim();
        current.qGuest = (m[4]||'').trim();
      } else {
        current.qNum = (line.match(/Q[\d-]+/)||[''])[0];
        current.qTitle = line.replace(/^\s*Q[\d-]+\s*\.\s*/, '').trim();
      }
    } else if(current){
      current.body.push(line);
    }
  }
  if(current) qs.push(current);
  qs.forEach(q=>{
    while(q.body.length && !q.body[0].trim()) q.body.shift();
    while(q.body.length && !q.body[q.body.length-1].trim()) q.body.pop();
  });
  return qs;
}

function onQCardEdit(editor){
  const tabIdx = parseInt(editor.dataset.tabIdx);
  const qIdx = parseInt(editor.dataset.qIdx);
  if(isNaN(tabIdx) || isNaN(qIdx)) return;
  if(!_wdbCardHtml[tabIdx]) _wdbCardHtml[tabIdx] = {};
  _wdbCardHtml[tabIdx][qIdx] = editor.innerHTML;
  const grid = document.getElementById('wdb-q-grid');
  if(!grid) return;
  const cards = grid.querySelectorAll('.q-card');
  const newBodyLines = [];
  cards.forEach(card=>{
    const header = card.dataset.rawHeader || '';
    const bodyEl = card.querySelector('.q-body');
    const bodyText = bodyEl ? bodyEl.innerText : '';
    if(header) newBodyLines.push(header);
    bodyText.split('\n').forEach(l=>newBodyLines.push(l));
    newBodyLines.push('');
  });
  const fullText = document.getElementById('wdb-input').value;
  const corners = parseWdb(fullText);
  if(!corners[tabIdx]) return;
  corners[tabIdx].body = newBodyLines;
  const rebuilt = corners.map(c=>{
    const header = c.number === '0' ? '' : '#'+c.number+' '+c.title;
    const body = (c.body||[]).join('\n');
    return header ? header+'\n'+body : body;
  }).join('\n\n');
  document.getElementById('wdb-input').value = rebuilt;
  if(window._wdbAutoSaveTimer) clearTimeout(window._wdbAutoSaveTimer);
  window._wdbAutoSaveTimer = setTimeout(()=>{ saveWdaebon(); }, 2000);
}

function renderWdb(){
  const text = document.getElementById('wdb-input').value;
  const corners = parseWdb(text);
  const box = document.getElementById('wdb-tabs-container');
  if(!corners.length){
    box.innerHTML = '';
    return;
  }
  if(_wdbActiveTab >= corners.length) _wdbActiveTab = 0;
  let html = '<div class="tab-bar" style="flex-wrap:wrap;">';
  corners.forEach((c,i)=>{
    html += '<button class="tab'+(i===_wdbActiveTab?' active':'')+'" onclick="wdbShowTab('+i+')">#'+c.number+' '+c.title+'</button>';
  });
  html += '</div>';
  html += '<div class="richtext-toolbar" data-target="wdb-tab-edit">';
  html += '<button onclick="rtCmd(this,\'bold\')" title="굵게 (Cmd/Ctrl+B)"><b>B</b></button>';
  html += '<button onclick="rtCmd(this,\'italic\')" title="기울임 (Cmd/Ctrl+I)"><i>I</i></button>';
  html += '<button onclick="rtCmd(this,\'underline\')" title="밑줄 (Cmd/Ctrl+U)"><u>U</u></button>';
  html += '<button onclick="rtCmd(this,\'strikeThrough\')" title="취소선"><s>S</s></button>';
  html += '<span class="rt-sep"></span>';
  html += '<button onclick="rtHighlight(this,\'#fff3a0\')" title="노란 하이라이트 (Cmd/Ctrl+Shift+H)" style="background:#fff3a0;">H</button>';
  html += '<button onclick="rtHighlight(this,\'#ffd6d6\')" title="분홍 하이라이트" style="background:#ffd6d6;">H</button>';
  html += '<button onclick="rtHighlight(this,\'#d6f0ff\')" title="파랑 하이라이트" style="background:#d6f0ff;">H</button>';
  html += '<button onclick="rtHighlight(this,\'transparent\')" title="하이라이트 제거" style="background:white;">✕</button>';
  html += '<span class="rt-sep"></span>';
  html += '<button onclick="rtColor(this,\'#d63031\')" title="빨간색" style="color:#d63031;">A</button>';
  html += '<button onclick="rtColor(this,\'#0984e3\')" title="파랑색 (Cmd/Ctrl+Shift+B)" style="color:#0984e3;">A</button>';
  html += '<button onclick="rtColor(this,\'#00b894\')" title="초록색 (Cmd/Ctrl+Shift+G)" style="color:#00b894;">A</button>';
  html += '<button onclick="rtColor(this,\'#2d3436\')" title="기본색">A</button>';
  html += '<span class="rt-sep"></span>';
  html += '<button onclick="rtSize(this,\'5\')" title="큰 글씨 (Cmd/Ctrl+Shift+L)" style="font-size:16px;">A⁺</button>';
  html += '<button onclick="rtSize(this,\'3\')" title="기본 크기">A</button>';
  html += '<button onclick="rtSize(this,\'2\')" title="작은 글씨" style="font-size:10px;">A⁻</button>';
  html += '<span class="rt-sep"></span>';
  html += '<button onclick="rtBox(this,\'pink\')" title="분홍 박스 (Cmd/Ctrl+Shift+K)" style="background:#FBEAF0;color:#993556;width:auto;padding:0 8px;font-size:10px;font-weight:700;">분홍</button>';
  html += '<button onclick="rtBox(this,\'blue\')" title="파랑 박스 (Cmd/Ctrl+Shift+E)" style="background:#E6F1FB;color:#185FA5;width:auto;padding:0 8px;font-size:10px;font-weight:700;">파랑</button>';
  html += '<span style="flex:1;"></span>';
  html += '<span style="font-size:10px;color:#7a8099;align-self:center;padding-right:6px;">⌘/Ctrl + B/I/U · ⇧+H(노랑) ⇧+L(크게) ⇧+B(파랑) ⇧+G(초록) ⇧+K(분홍박스) ⇧+E(파랑박스)</span>';
  html += '</div>';
  const active = corners[_wdbActiveTab] || corners[0];
  if(active.guests && active.guests.length){
    html += '<div style="margin-bottom:10px;padding:10px 14px;background:linear-gradient(135deg,#fdfbf5,#fff);border-radius:10px;border:1.5px solid #e8b84b;display:flex;flex-wrap:wrap;gap:10px;align-items:center;">';
    html += '<span style="font-size:11px;font-weight:700;color:#e8b84b;letter-spacing:.05em;">👤 출연자</span>';
    active.guests.forEach(g=>{
      html += '<span style="display:inline-flex;align-items:center;gap:6px;padding:4px 10px;background:white;border-radius:20px;border:1px solid #e8b84b;">';
      html += '<span style="font-size:13px;font-weight:700;color:#1a1d23;">'+g.name+'</span>';
      if(g.title){
        html += '<span style="font-size:11px;color:#7a8099;">|</span>';
        html += '<span style="font-size:12px;color:#636e72;">'+g.title+'</span>';
      }
      html += '</span>';
    });
    html += '</div>';
  }
  const activeBody = active.body || [];
  if(hasQPattern(activeBody)){
    const questions = parseQuestions(activeBody);
    html += '<div class="q-grid" id="wdb-q-grid">';
    questions.forEach((q, qIdx)=>{
      const typeCls = getQTypeClass(q.qType);
      const typeTagCls = typeCls === 'qt-trust' ? 't-trust' : typeCls === 'qt-connect' ? 't-connect' : typeCls === 'qt-impact' ? 't-impact' : '';
      html += '<div class="q-card '+typeCls+'"'
        + ' data-q-idx="'+qIdx+'"'
        + ' data-raw-header="'+escapeAttr(q.header)+'">';
      html += '<div class="q-card-head">';
      if(q.qNum) html += '<span class="q-num">'+escapeHtml(q.qNum)+'</span>';
      if(q.qType) html += '<span class="q-type '+typeTagCls+'">'+escapeHtml(q.qType)+'</span>';
      if(q.qGuest) html += '<span class="q-guest">'+escapeHtml(q.qGuest)+'</span>';
      html += '</div>';
      if(q.qTitle) html += '<div class="q-title">'+escapeHtml(q.qTitle)+'</div>';
      const bodyText = (q.body||[]).join('\n');
      const cachedHtml = (_wdbCardHtml[_wdbActiveTab] || {})[qIdx];
      let bodyHtml;
      if(cachedHtml !== undefined){
        const tmp = document.createElement('div');
        tmp.innerHTML = cachedHtml;
        const cachedText = (tmp.innerText||'').replace(/\s+/g,'').trim();
        const currentText = bodyText.replace(/\s+/g,'').trim();
        if(cachedText === currentText){
          bodyHtml = cachedHtml;
        } else {
          bodyHtml = escapeHtml(bodyText).replace(/\n/g,'<br>');
          delete _wdbCardHtml[_wdbActiveTab][qIdx];
        }
      } else {
        bodyHtml = escapeHtml(bodyText).replace(/\n/g,'<br>');
      }
      html += '<div class="q-body" contenteditable="true"'
        + ' data-q-idx="'+qIdx+'" data-tab-idx="'+_wdbActiveTab+'"'
        + ' oninput="onQCardEdit(this)"'
        + ' data-placeholder="본문을 입력하세요...">'+ bodyHtml +'</div>';
      html += '</div>';
    });
    html += '</div>';
  } else {
    const activeText = activeBody.join('\n').replace(/^\s*\n|\n\s*$/g,'') || '';
    const savedHtml = _wdbTabHtml[_wdbActiveTab];
    const initialHtml = savedHtml || activeText.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>');
    html += '<div class="rich-editor" id="wdb-tab-edit" contenteditable="true" data-idx="'+_wdbActiveTab+'" '+
    'style="min-height:240px;font-size:13px;line-height:1.7;border-left:3px solid #e8b84b;background:#fdfbf5;" '+
    'oninput="onWdbTabEdit(this)" data-placeholder="이 코너 내용을 직접 편집하세요...">'+
    initialHtml + '</div>';
  }
  box.innerHTML = html;
}

let _wdbTabHtml = {};
let _wdbCardHtml = {};

function onWdbTabEdit(editor){
  const idx = parseInt(editor.dataset.idx);
  _wdbTabHtml[idx] = editor.innerHTML;
  const plain = editor.innerText;
  const fullText = document.getElementById('wdb-input').value;
  const corners = parseWdb(fullText);
  if(!corners[idx]) return;
  corners[idx].body = plain.split('\n');
  const rebuilt = corners.map(c=>{
    const header = c.number === '0' ? '' : '#'+c.number+' '+c.title;
    const body = c.body.join('\n');
    return header ? header+'\n'+body : body;
  }).join('\n\n');
  document.getElementById('wdb-input').value = rebuilt;
  
  if(window._wdbAutoSaveTimer) clearTimeout(window._wdbAutoSaveTimer);
  window._wdbAutoSaveTimer = setTimeout(()=>{
    saveWdaebon();
  }, 2000);
}

function wdbShowTab(i){
  _wdbActiveTab = i;
  renderWdb();
}

async function saveWdaebon(){
  const badge=document.getElementById('wdb-badge');
  try{
    const currentEditor = document.getElementById('wdb-tab-edit');
    if(currentEditor){
      const idx = parseInt(currentEditor.dataset.idx);
      _wdbTabHtml[idx] = currentEditor.innerHTML;
    }
    const payload = {
      text: document.getElementById('wdb-input').value,
      tabHtml: _wdbTabHtml,
      cardHtml: _wdbCardHtml
    };
    const payloadStr = JSON.stringify(payload);
    
    if(payloadStr.length > 500000){
      alert('⚠️ 데이터가 너무 커요 (' + Math.round(payloadStr.length/1024) + 'KB). 일부 내용을 줄이거나 분할 저장해주세요.');
      return;
    }
    
    const res = await fetch('/api/post/wdaebon',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({content:payloadStr,date:new Date().toISOString().slice(0,10)})
    });
    
    if(!res.ok){
      const errText = await res.text();
      alert('❌ 저장 실패 (HTTP ' + res.status + ')\n' + errText.slice(0, 200));
      return;
    }
    
    badge.textContent = '✓ 저장됨 (' + new Date().toLocaleTimeString('ko-KR', {hour: '2-digit', minute: '2-digit', second: '2-digit'}) + ')';
    badge.style.display='inline';
    badge.style.color = '#00b894';
    setTimeout(()=>badge.style.display='none', 4000);
  }catch(e){
    alert('❌ 저장 오류: ' + e.message);
    badge.textContent = '❌ 저장 실패';
    badge.style.color = '#d63031';
    badge.style.display = 'inline';
  }
}

async function clearWdaebon(){
  if(!confirm('완대본 플로우 초기화할까요?'))return;
  await fetch('/api/post/wdaebon',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({content:'',date:new Date().toISOString().slice(0,10)})
  });
  document.getElementById('wdb-input').value='';
  document.getElementById('wdb-tabs-container').innerHTML='';
  _wdbTabHtml = {};
  _wdbCardHtml = {};
  _wdbActiveTab = 0;
}

async function loadWdaebon(){
  try{
    const d=await fetch('/api/post/wdaebon').then(r=>r.json());
    if(d.content){
      let text = d.content;
      try{
        const payload = JSON.parse(d.content);
        if(payload && typeof payload === 'object' && payload.text !== undefined){
          text = payload.text;
          _wdbTabHtml = payload.tabHtml || {};
          _wdbCardHtml = payload.cardHtml || {};
        }
      }catch(e){}
      document.getElementById('wdb-input').value = text;
      renderWdb();
    }
  }catch(e){}
}

async function loadCalendar(){
  try{
    const d=await fetch('/api/post/calendar').then(r=>r.json());
    if(d.content){
      const data=JSON.parse(d.content);
      renderCalendar(data);
    }
  }catch(e){}
}

async function searchEncyclopedia(){
  const q = document.getElementById('enc-input').value.trim();
  if(!q) return;
  const btn = document.getElementById('enc-btn');
  const result = document.getElementById('enc-result');
  btn.classList.add('ls'); btn.innerHTML='<span class="spinner"></span>';
  result.innerHTML='<span class="content-empty">검색 중...</span>';
  try{
    const d = await fetch('/api/encyclopedia/search?q='+encodeURIComponent(q)).then(r=>r.json());
    if(d.error){
      result.innerHTML='<span class="content-empty">오류: '+d.error+'</span>';
      return;
    }
    if(!d.results || !d.results.length){
      result.innerHTML='<span class="content-empty">검색 결과가 없어요.</span>';
      return;
    }
    let html = '<div style="font-size:11px;color:#7a8099;margin-bottom:10px;">'+d.results.length+'건 검색됨</div>';
    d.results.forEach(row=>{
      html += '<div style="padding:12px 14px;background:#f8f9fa;border-radius:10px;margin-bottom:8px;border-left:3px solid #0984e3;">';
      const 상황 = row['상황'] || '';
      const 주제 = row['주제(시장/섹터/종목)'] || row['주제'] || '';
      const 표현 = row['표현'] || '';
      if(상황) html += '<div style="font-size:11px;font-weight:700;color:#7a8099;margin-bottom:4px;">📌 '+상황+(주제?' · '+주제:'')+'</div>';
      if(표현) html += '<div style="font-size:14px;color:#1a1d23;line-height:1.7;">'+표현+'</div>';
      html += '</div>';
    });
    result.innerHTML = html;
  }catch(e){
    result.innerHTML='<span class="content-empty">네트워크 오류</span>';
  }
  btn.classList.remove('ls'); btn.innerHTML='검색';
}

async function uploadPDFs(input){
  const files = Array.from(input.files);
  for(const file of files){
    const reader = new FileReader();
    reader.onload = async(e)=>{
      const base64 = e.target.result.split(',')[1];
      await fetch('/api/pdf/upload',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name: file.name, data: base64})
      });
      loadPDFList();
    };
    reader.readAsDataURL(file);
  }
  input.value='';
}

async function loadPDFList(){
  const d = await fetch('/api/pdf/list').then(r=>r.json());
  const tabs = document.getElementById('pdf-tabs');
  if(!d.pdfs||!d.pdfs.length){
    tabs.innerHTML='';
    document.getElementById('pdf-viewer').innerHTML='<span class="content-empty">PDF를 업로드해주세요</span>';
    return;
  }
  tabs.innerHTML = d.pdfs.map((p,i)=>
    '<div style="display:flex;align-items:center;gap:2px;">'+
    '<button class="tab'+(i===0?' active':'')+'" onclick="showPDF('+p.id+',this)" id="pdf-tab-'+p.id+'">'+p.name.replace('.pdf','').slice(0,15)+'</button>'+
    '<button onclick="deletePDF('+p.id+')" style="border:none;background:none;color:#b2bec3;cursor:pointer;font-size:12px;padding:0 2px;">✕</button>'+
    '</div>'
  ).join('');
  if(d.pdfs.length) showPDF(d.pdfs[0].id, document.querySelector('#pdf-tabs .tab'));
}

async function showPDF(id, btn){
  document.querySelectorAll('#pdf-tabs .tab').forEach(b=>b.classList.remove('active'));
  if(btn) btn.classList.add('active');
  const viewer = document.getElementById('pdf-viewer');
  viewer.innerHTML='<span class="content-empty">로딩 중...</span>';
  const d = await fetch('/api/pdf/'+id).then(r=>r.json());
  if(d.data){
    viewer.innerHTML='<iframe src="data:application/pdf;base64,'+d.data+'" style="width:100%;height:500px;border:none;"></iframe>';
  }
}

async function clearAllPDFs(){
  if(!confirm('리포트 전체 삭제할까요?')) return;
  const d = await fetch('/api/pdf/list').then(r=>r.json());
  for(const p of (d.pdfs||[])){
    await fetch('/api/pdf/'+p.id+'/delete',{method:'POST'});
  }
  loadPDFList();
}

async function deletePDF(id){
  await fetch('/api/pdf/'+id+'/delete',{method:'POST'});
  loadPDFList();
}

async function searchThemePrices(){
  const q = document.getElementById('theme-input').value.trim();
  if(!q) return;
  const btn = document.getElementById('theme-price-btn');
  const result = document.getElementById('theme-price-result');
  btn.classList.add('ls'); btn.innerHTML='<span class="spinner"></span> 조회 중...';
  result.innerHTML='<span class="content-empty">시세 불러오는 중... (종목 수에 따라 10~20초 소요)</span>';
  try{
    const d = await fetch('/api/theme/prices?q='+encodeURIComponent(q)).then(r=>r.json());
    if(d.error){
      result.innerHTML='<span class="content-empty">오류: '+d.error+'</span>';
      return;
    }
    if(!d.stocks||!d.stocks.length){
      result.innerHTML='<span class="content-empty">"'+q+'" 테마 종목이 없어요.</span>';
      return;
    }
    let html = '<div style="font-size:11px;color:#7a8099;margin-bottom:10px;">'+d.query+' · '+d.count+'종목</div>';
    html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:8px;">';
    d.stocks.forEach(s=>{
      const color = s.up === true ? '#d63031' : s.up === false ? '#0984e3' : '#636e72';
      html += '<div style="padding:10px 12px;background:#f8f9fa;border-radius:10px;border-top:3px solid '+color+';">';
      html += '<div style="font-size:13px;font-weight:700;color:#1a1d23;margin-bottom:4px;">'+s.name+'</div>';
      html += '<div style="font-size:16px;font-weight:700;color:#1a1d23;font-family:DM Mono,monospace;">'+s.price+'</div>';
      html += '<div style="font-size:12px;color:'+color+';font-weight:600;">'+s.change+' ('+s.pct+')</div>';
      html += '<a href="https://m.stock.naver.com/domestic/stock/'+s.code+'/chart" target="_blank" style="font-size:10px;color:#b2bec3;">차트 →</a>';
      html += '</div>';
    });
    html += '</div>';
    result.innerHTML = html;
  }catch(e){
    result.innerHTML='<span class="content-empty">네트워크 오류</span>';
  }
  btn.classList.remove('ls'); btn.innerHTML='조회';
}

async function addEncyc(){
  const exprEl = document.getElementById('encyc-add-expr');
  const sitEl = document.getElementById('encyc-add-situation');
  const topEl = document.getElementById('encyc-add-topic');
  const msgEl = document.getElementById('encyc-add-msg');
  const btn = document.getElementById('encyc-add-btn');
  const expr = (exprEl.value||'').trim();
  if(!expr){
    msgEl.textContent = '표현은 필수에요';
    msgEl.style.color = '#d63031';
    exprEl.focus();
    return;
  }
  btn.disabled = true;
  btn.textContent = '추가중…';
  msgEl.textContent = '';
  try{
    const r = await fetch('/api/encyc/add', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        '상황': (sitEl.value||'').trim(),
        '주제': (topEl.value||'').trim(),
        '표현': expr
      })
    });
    const d = await r.json();
    if(d && d.ok){
      msgEl.textContent = '✓ 추가됨 — '+expr.slice(0,30)+(expr.length>30?'…':'');
      msgEl.style.color = '#00b894';
      // 입력 폼 초기화 (상황/주제는 유지 — 연달아 같은 카테고리 입력하기 편하게)
      exprEl.value = '';
      exprEl.focus();
      // 5초 후 메시지 사라짐
      setTimeout(()=>{ if(msgEl.textContent.startsWith('✓')) msgEl.textContent=''; }, 5000);
    } else {
      msgEl.textContent = '❌ '+(d && d.error ? d.error : '추가 실패');
      msgEl.style.color = '#d63031';
    }
  } catch(e){
    msgEl.textContent = '❌ 네트워크 오류 — '+String(e);
    msgEl.style.color = '#d63031';
  } finally {
    btn.disabled = false;
    btn.textContent = '＋ 추가';
  }
}

async function searchEncyc(){  const q = document.getElementById('encyc-input').value.trim();
  if(!q) return;
  const btn = document.getElementById('encyc-btn');
  const result = document.getElementById('encyc-result');
  btn.classList.add('ls'); btn.innerHTML='<span class="spinner"></span>';
  result.innerHTML='<span class="content-empty">검색 중...</span>';
  try{
    const d = await fetch('/api/encyc/search?q='+encodeURIComponent(q)).then(r=>r.json());
    if(d.error){
      result.innerHTML='<span class="content-empty">오류: '+d.error+'</span>';
      return;
    }
    if(!d.results||!d.results.length){
      result.innerHTML='<span class="content-empty">검색 결과가 없어요.</span>';
      return;
    }
    let html = '<div style="font-size:11px;color:#7a8099;margin-bottom:10px;">'+d.results.length+'건</div>';
    d.results.forEach(row=>{
      html += '<div style="padding:12px 14px;background:#f8f9fa;border-radius:10px;margin-bottom:8px;border-left:3px solid #0984e3;">';
      const situation = row['상황'] || '';
      const topic = row['주제(시장/섹터/종목)'] || row['주제'] || '';
      const expr = row['표현'] || '';
      if(situation) html += '<div style="font-size:11px;font-weight:700;color:#7a8099;margin-bottom:4px;">📌 '+situation+'</div>';
      if(topic) html += '<div style="font-size:12px;color:#0984e3;font-weight:600;margin-bottom:6px;">'+topic+'</div>';
      if(expr) html += '<div style="font-size:14px;color:#1a1d23;line-height:1.7;">'+expr+'</div>';
      html += '</div>';
    });
    result.innerHTML = html;
  }catch(e){
    result.innerHTML='<span class="content-empty">네트워크 오류</span>';
  }
  btn.classList.remove('ls'); btn.innerHTML='검색';
}

// === CG DB 검색 ===
function _cgdbTypeColor(type){
  // 유형별 색깔 배지
  const map = {
    '발언':'#A32D2D','지표':'#0C447C','금리':'#854F0B','등락':'#085041',
    '일정':'#5F5E5A','실적':'#3C3489','변동성':'#A32D2D','밸류에이션':'#0C447C',
    '코멘트':'#085041','정책':'#412402','전망':'#3C3489'
  };
  return map[type] || '#5F5E5A';
}
function _cgdbTypeBg(type){
  const map = {
    '발언':'#FCEBEB','지표':'#E6F1FB','금리':'#FDF1DF','등락':'#E1F5EE',
    '일정':'#F1EFE5','실적':'#EEEDFE','변동성':'#FCEBEB','밸류에이션':'#E6F1FB',
    '코멘트':'#E1F5EE','정책':'#FAEEDA','전망':'#EEEDFE'
  };
  return map[type] || '#F1EFE5';
}
function _renderCGDBResults(d, fromRecent){
  const result = document.getElementById('cgdb-result');
  if(d.error){ result.innerHTML = '<span class="content-empty">오류: '+d.error+'</span>'; return; }
  if(!d.results || !d.results.length){
    result.innerHTML = '<span class="content-empty">'+(fromRecent?'CG DB가 비어있어요':'매칭되는 CG가 없어요')+'</span>';
    return;
  }
  const header = fromRecent
    ? '<div style="font-size:11px;color:#7a8099;margin-bottom:10px;">최근 '+d.results.length+'건 (전체 '+(d.total||d.results.length)+'건)</div>'
    : '<div style="font-size:11px;color:#7a8099;margin-bottom:10px;">"'+(d.query||'').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]))+'" 검색 결과 '+d.results.length+'건</div>';
  let html = header;
  d.results.forEach(row => {
    const date = row['날짜'] || '';
    const title = row['CG 제목'] || row['제목'] || '';
    const keyword = row['키워드'] || '';
    const type = row['유형'] || '';
    const typeColor = _cgdbTypeColor(type);
    const typeBg = _cgdbTypeBg(type);
    const esc = s => String(s||'').replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));
    html += '<div style="display:flex;align-items:center;gap:10px;padding:9px 12px;background:#fff;border:0.5px solid #E5E1D6;border-radius:8px;margin-bottom:6px;">';
    html += '<span style="font-size:10px;color:#888780;font-family:\'DM Mono\',monospace;white-space:nowrap;min-width:74px;">'+esc(date)+'</span>';
    if(type) html += '<span style="font-size:10px;padding:2px 8px;border-radius:20px;background:'+typeBg+';color:'+typeColor+';font-weight:500;white-space:nowrap;">'+esc(type)+'</span>';
    html += '<span style="font-size:13px;color:#1a1d23;flex:1;line-height:1.4;font-weight:500;">'+esc(title)+'</span>';
    if(keyword) html += '<span style="font-size:11px;color:#7a8099;font-style:italic;white-space:nowrap;max-width:200px;overflow:hidden;text-overflow:ellipsis;" title="'+esc(keyword)+'">'+esc(keyword)+'</span>';
    html += '</div>';
  });
  result.innerHTML = html;
}
async function searchCGDB(){
  const q = document.getElementById('cgdb-input').value.trim();
  if(!q){ loadRecentCGDB(); return; }
  const btn = document.getElementById('cgdb-btn');
  const result = document.getElementById('cgdb-result');
  btn.classList.add('ls'); btn.innerHTML = '<span class="spinner"></span>';
  result.innerHTML = '<span class="content-empty">검색 중...</span>';
  try{
    const d = await fetch('/api/cgdb/search?q='+encodeURIComponent(q)).then(r=>r.json());
    _renderCGDBResults(d, false);
  }catch(e){
    result.innerHTML = '<span class="content-empty">네트워크 오류</span>';
  }
  btn.classList.remove('ls'); btn.innerHTML = '검색';
}
async function loadRecentCGDB(){
  const result = document.getElementById('cgdb-result');
  result.innerHTML = '<span class="content-empty">불러오는 중...</span>';
  try{
    const d = await fetch('/api/cgdb/search?q=').then(r=>r.json());
    _renderCGDBResults(d, true);
  }catch(e){
    result.innerHTML = '<span class="content-empty">네트워크 오류</span>';
  }
}

async function searchGuest(){
  const q = document.getElementById('guest-input').value.trim();
  if(!q) return;
  const btn = document.getElementById('guest-btn');
  const result = document.getElementById('guest-result');
  btn.classList.add('ls'); btn.innerHTML='<span class="spinner"></span>';
  result.innerHTML='<span class="content-empty">검색 중...</span>';
  try{
    const d = await fetch('/api/guest/search?q='+encodeURIComponent(q)).then(r=>r.json());
    if(d.error){
      result.innerHTML='<span class="content-empty">오류: '+d.error+'</span>';
      return;
    }
    const sheets = Object.keys(d.results||{});
    if(!sheets.length){
      result.innerHTML='<span class="content-empty">검색 결과가 없어요.</span>';
      return;
    }
    let html = '';
    sheets.forEach(sheet=>{
      const rows = d.results[sheet];
      html += '<div style="margin-bottom:20px;">';
      html += '<div style="font-size:11px;font-weight:700;color:#e8b84b;letter-spacing:.08em;margin-bottom:10px;padding-bottom:6px;border-bottom:2px solid #e8b84b;">📋 '+sheet+' · '+rows.length+'건</div>';
      rows.forEach(row=>{
        html += '<div style="padding:12px 14px;background:#f8f9fa;border-radius:10px;margin-bottom:8px;border-left:3px solid #e8b84b;">';
        const entries = Object.entries(row).filter(([k,v])=>v&&v.trim());
        const priority = ['날짜','이름','코너'];
        const header = priority.map(k=>{
          const e = entries.find(([ek])=>ek===k);
          return e ? '<span style="font-size:12px;font-weight:700;color:#1a1d23;">'+e[1]+'</span>' : '';
        }).filter(Boolean).join(' · ');
        if(header) html += '<div style="margin-bottom:6px;">'+header+'</div>';
        entries.forEach(([k,v])=>{
          if(priority.includes(k)) return;
          html += '<div style="margin-bottom:3px;">';
          html += '<span style="font-size:11px;color:#7a8099;font-weight:700;margin-right:6px;">'+k+'</span>';
          if(v.startsWith('http')){
            html += '<a href="'+v+'" target="_blank" style="font-size:12px;color:#0984e3;">🔗 링크</a>';
          } else {
            html += '<span style="font-size:13px;color:#2d3436;">'+v+'</span>';
          }
          html += '</div>';
        });
        html += '</div>';
      });
      html += '</div>';
    });
    result.innerHTML = html;
  }catch(e){
    result.innerHTML='<span class="content-empty">네트워크 오류</span>';
  }
  btn.classList.remove('ls'); btn.innerHTML='검색';
}
const CP_SECTIONS = {
  indicator: ['📊지표','📊 지표','📌지표','📌 지표'],
  us_market: ['🇺🇸美증시 마감','🇺🇸美증시','🇺🇸 美증시 마감','🇺🇸 美증시','美증시 마감','미증시 마감'],
  signal: ['📡시장 시그널','📡 시장 시그널','📡시장시그널','📡 시장시그널','시장 시그널'],
  sector: ['📌Sector','📌sector','📌 Sector','📌 sector','📌섹터','📌 섹터'],
  kospi: ['📌코스피','📌 코스피'],
  kosdaq: ['📌코스닥','📌 코스닥']
};
const CL_SECTIONS = {
  figure: ['📌 마감수치','📌마감수치'],
  factor: ['📌 지수 팩터','📌지수 팩터','📌 지수팩터','📌지수팩터'],
  supply: ['📌 수급','📌수급'],
  sector: ['📌 특징 업종','📌특징 업종','📌특징업종','📌 특징업종'],
  stock: ['📌 특징주','📌특징주'],
  schedule: ['📌 내일 일정','📌내일 일정','📌내일일정']
};

function parseSection(text, headers){
  if(!text) return null;
  const lines = text.split('\n');
  let capturing = false, result = [];
  let headerAdded = false;
  for(let i=0;i<lines.length;i++){
    const l = lines[i];
    const isHeader = headers.some(h=>l.includes(h));
    const isOtherHeader = !isHeader && /^(📌|📊|🇺🇸|📡)/.test(l);
    if(isHeader){
      // 같은 헤더가 본문에 여러 번 등장하면: 헤더 줄은 한 번만, 캡쳐는 다시 시작
      if(!headerAdded){ result.push(l); headerAdded = true; }
      capturing = true;
      continue;
    }
    if(isOtherHeader){
      // 다른 헤더 만나면 캡쳐 잠시 멈춤. 같은 헤더 다시 만날 수 있으니 break 안 함.
      capturing = false;
      continue;
    }
    if(capturing){
      result.push(l);
    }
  }
  return result.length ? result.join('\n') : null;
}

let _cpRaw = '', _clRaw = '';

function _pctColor(pctStr){
  if(!pctStr) return '';
  const num = parseFloat(pctStr.replace(/[^\d.\-+]/g,''));
  if(isNaN(num)) return '';
  if(num > 0) return '#A32D2D';
  if(num < 0) return '#185FA5';
  return 'var(--color-text-secondary)';
}

function _escapeHtml(s){
  return String(s||'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function _linkifyText(text){
  // 1. HTML escape (XSS 차단)
  let s = _escapeHtml(text);
  const linkStyle = 'color:#1a73e8;text-decoration:none;border-bottom:1px dotted #1a73e8;font-weight:500;';
  // 2. 봇 자체 마커 [[LINK:URL]] → 🔗 링크
  s = s.replace(/\[\[LINK:(https?:\/\/[^\]\s]+)\]\]/g, function(_, url){
    const safeUrl = url.replace(/"/g, '&quot;');
    return '<a href="'+safeUrl+'" target="_blank" rel="noopener noreferrer" style="'+linkStyle+'">🔗</a>';
  });
  // 3. markdown [label](URL) → 안전한 <a> (http/https만 허용)
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s\)]+)\)/g, function(_, label, url){
    const safeUrl = url.replace(/"/g, '&quot;');
    return '<a href="'+safeUrl+'" target="_blank" rel="noopener noreferrer" style="'+linkStyle+'">'+label+'</a>';
  });
  // 4. 평문 URL (http/https) → 🔗 링크 — 이미 <a href> 안에 들어간 건 제외
  s = s.replace(/(?<!href=")(https?:\/\/[^\s<]+)/g, function(url){
    const safeUrl = url.replace(/"/g, '&quot;');
    return '<a href="'+safeUrl+'" target="_blank" rel="noopener noreferrer" style="'+linkStyle+'">🔗</a>';
  });
  return s;
}

function _formatStockLine(line){
  let s = _linkifyText(line);
  // % 색깔 처리 (escape된 결과 위에서 매칭)
  s = s.replace(/([+-]?\d+\.?\d*%)/g, function(pct){
    const color = _pctColor(pct);
    return '<span style="color:'+color+';font-weight:500;">'+pct+'</span>';
  });
  return s;
}

function _parseSectorCards(text){
  const cards = [];
  const lines = text.split('\n');
  let cur = null;
  for(const raw of lines){
    let l = raw.trim();
    if(!l) continue;
    if(/^(📌|📊|🇺🇸|📡)/.test(l)) continue;
    if(/^[✔️✔✓☑️☑✅]\s*/.test(l)){
      if(cur) cards.push(cur);
      const name = l.replace(/^[✔️✔✓☑️☑✅\s]+/,'').trim();
      cur = {name: name, bullets:[], stocks:[]};
      continue;
    }
    if(!cur) continue;
    if(l.includes('관련 종목') || l.includes('관련종목')){
      const after = l.replace(/^.*?관련\s*종목\s*[:：—-]?\s*/,'').trim();
      const parts = after.split(/[,，]/).map(s=>s.trim()).filter(Boolean);
      cur.stocks.push(...parts);
      continue;
    }
    const bulletParts = l.split(/\s+[•・]\s+/).map(s=>s.trim()).filter(Boolean);
    if(bulletParts.length > 1){
      bulletParts.forEach(p=>{
        cur.bullets.push(p.replace(/^[-•・*]\s*/,''));
      });
    } else if(/^[-•・*]/.test(l)){
      cur.bullets.push(l.replace(/^[-•・*]\s*/,''));
    } else {
      cur.bullets.push(l);
    }
  }
  if(cur) cards.push(cur);
  return cards;
}

function _parseStockCards(text){
  const cards = [];
  const lines = text.split('\n');
  let cur = null;
  const noiseKeywords = ['많이 본 기사','기자 구독','구독하기','로그인','회원가입','글자크기','스크랩','광고'];
  for(const raw of lines){
    const l = raw.trim();
    if(!l){
      if(cur && (cur.name || cur.bullets.length)){
        cards.push(cur);
        cur = null;
      }
      continue;
    }
    if(/^(📌|📊|🇺🇸|📡)/.test(l)) continue;
    if(noiseKeywords.some(k => l.includes(k))) continue;
    if(l.startsWith('-')||l.startsWith('•')){
      if(!cur) cur = {name:'',bullets:[]};
      cur.bullets.push(l.replace(/^[-•]\s*/,''));
    } else {
      if(cur) cards.push(cur);
      cur = {name: l, bullets:[]};
    }
  }
  if(cur && (cur.name || cur.bullets.length)) cards.push(cur);
  return cards;
}

function _parseIndicatorCards(text){
  const cards = [];
  const lines = text.split('\n');
  for(const raw of lines){
    const l = raw.trim();
    if(!l) continue;
    if(/^(📌|📊|🇺🇸|📡)/.test(l)) continue;
    const m = l.match(/^([A-Za-z가-힣0-9&·]+(?:\s+[A-Za-z가-힣0-9&·]+)?)\s+(.+)$/);
    if(m){
      const [, name, rest] = m;
      const pctMatch = rest.match(/([+-]?\d+\.?\d*%)/);
      const valMatch = rest.match(/([\d,]+\.?\d*)\s*(?=\(|$|\s)/);
      cards.push({
        name: name.trim(),
        value: valMatch ? valMatch[1] : '',
        pct: pctMatch ? pctMatch[1] : '',
      });
    } else {
      cards.push({name:l, value:'', pct:''});
    }
  }
  return cards;
}

function _renderSectorCards(cards){
  if(!cards.length) return '<span class="content-empty">섹터 정보가 없어요</span>';
  return '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-top:8px;">' +
    cards.map(c=>{
      let html = '<div style="background:#fff;border:1px solid #e8b84b33;border-radius:10px;padding:12px 14px;">';
      html += '<div style="font-size:11px;color:#7a8099;font-weight:600;margin-bottom:4px;">섹터</div>';
      html += '<div style="font-size:14px;font-weight:600;color:#1a1d23;margin-bottom:8px;">✔️ '+_linkifyText(c.name)+'</div>';
      if(c.bullets.length){
        html += '<div style="font-size:12px;color:#2d3436;line-height:1.6;margin-bottom:8px;">'+c.bullets.map(b=>'• '+_linkifyText(b)).join('<br>')+'</div>';
      }
      if(c.stocks.length){
        html += '<div style="font-size:11px;color:#7a8099;font-weight:600;margin-top:6px;">관련 종목</div>';
        html += '<div style="font-size:12px;line-height:1.6;color:#2d3436;">'+c.stocks.map(_formatStockLine).join('<br>')+'</div>';
      }
      html += '</div>';
      return html;
    }).join('') + '</div>';
}

function _renderSignalCards(cards){
  if(!cards.length) return '<span class="content-empty">시그널 정보가 없어요</span>';
  return '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-top:8px;">' +
    cards.map(c=>{
      let html = '<div style="background:#fff;border:1px solid #e8b84b33;border-radius:10px;padding:12px 14px;">';
      html += '<div style="font-size:11px;color:#7a8099;font-weight:600;margin-bottom:4px;">시그널</div>';
      html += '<div style="font-size:14px;font-weight:600;color:#1a1d23;margin-bottom:8px;">☑️ '+_linkifyText(c.name)+'</div>';
      if(c.bullets.length){
        html += '<div style="font-size:12px;color:#2d3436;line-height:1.6;">'+c.bullets.map(b=>'• '+_linkifyText(b)).join('<br>')+'</div>';
      }
      html += '</div>';
      return html;
    }).join('') + '</div>';
}

function _renderStockCards(cards, label){
  if(!cards.length) return '<span class="content-empty">'+label+' 정보가 없어요</span>';
  return '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin-top:8px;">' +
    cards.map(c=>{
      const nameHtml = _formatStockLine(c.name);
      let html = '<div style="background:#fff;border:1px solid #e8b84b33;border-radius:10px;padding:12px 14px;">';
      html += '<div style="font-size:11px;color:#7a8099;font-weight:600;margin-bottom:4px;">'+label+'</div>';
      html += '<div style="font-size:14px;font-weight:600;color:#1a1d23;margin-bottom:8px;">'+nameHtml+'</div>';
      if(c.bullets.length){
        html += '<div style="font-size:12px;color:#2d3436;line-height:1.6;">'+c.bullets.map(b=>'• '+_linkifyText(b)).join('<br>')+'</div>';
      }
      html += '</div>';
      return html;
    }).join('') + '</div>';
}

function _renderIndicatorCards(cards){
  if(!cards.length) return '<span class="content-empty">지표가 없어요</span>';
  return '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin-top:8px;">' +
    cards.map(c=>{
      const color = _pctColor(c.pct);
      let html = '<div style="background:#fff;border:1px solid #e8b84b33;border-radius:10px;padding:10px 12px;">';
      html += '<div style="font-size:11px;color:#7a8099;font-weight:600;margin-bottom:4px;">'+c.name+'</div>';
      if(c.value){
        html += '<div style="font-size:15px;font-weight:600;color:#1a1d23;font-family:DM Mono,monospace;">'+c.value+'</div>';
      }
      if(c.pct){
        html += '<div style="font-size:12px;color:'+color+';font-weight:500;">'+c.pct+'</div>';
      }
      html += '</div>';
      return html;
    }).join('') + '</div>';
}

function _renderTextBlock(text){
  const lines = text.split('\n').map(l => l.trim()).filter(l => l && !/^(📌|📊|🇺🇸|📡)/.test(l));
  if(!lines.length) return '<span class="content-empty">내용이 없어요</span>';
  return '<div style="background:#fff;border:1px solid #e8b84b33;border-radius:10px;padding:12px 14px;margin-top:8px;">' +
    lines.map(l => '<div style="font-size:13px;color:#2d3436;line-height:1.7;padding:2px 0;">'+_formatStockLine(l)+'</div>').join('') +
    '</div>';
}

function cpTab(btn, key){
  document.querySelectorAll('#cp-tabs .tab').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  const el = document.getElementById('checkpoint-body');
  if(!_cpRaw){ return; }
  if(key==='all'){
    let html = '';
    const ind = parseSection(_cpRaw, CP_SECTIONS.indicator);
    if(ind){
      html += '<div style="font-size:11px;color:#7a8099;font-weight:700;letter-spacing:.08em;margin:6px 0 4px;">📊 지표</div>';
      html += _renderIndicatorCards(_parseIndicatorCards(ind));
    }
    const usm = parseSection(_cpRaw, CP_SECTIONS.us_market);
    if(usm){
      html += '<div style="font-size:11px;color:#7a8099;font-weight:700;letter-spacing:.08em;margin:14px 0 4px;">🇺🇸 미증시 마감</div>';
      html += _renderTextBlock(usm);
    }
    const sig = parseSection(_cpRaw, CP_SECTIONS.signal);
    if(sig){
      html += '<div style="font-size:11px;color:#7a8099;font-weight:700;letter-spacing:.08em;margin:14px 0 4px;">📡 시장 시그널</div>';
      html += _renderSignalCards(_parseSectorCards(sig));
    }
    const sec = parseSection(_cpRaw, CP_SECTIONS.sector);
    if(sec){
      html += '<div style="font-size:11px;color:#7a8099;font-weight:700;letter-spacing:.08em;margin:14px 0 4px;">📌 섹터</div>';
      html += _renderSectorCards(_parseSectorCards(sec));
    }
    const kp = parseSection(_cpRaw, CP_SECTIONS.kospi);
    if(kp){
      html += '<div style="font-size:11px;color:#7a8099;font-weight:700;letter-spacing:.08em;margin:14px 0 4px;">📌 코스피</div>';
      html += _renderStockCards(_parseStockCards(kp), '코스피');
    }
    const kd = parseSection(_cpRaw, CP_SECTIONS.kosdaq);
    if(kd){
      html += '<div style="font-size:11px;color:#7a8099;font-weight:700;letter-spacing:.08em;margin:14px 0 4px;">📌 코스닥</div>';
      html += _renderStockCards(_parseStockCards(kd), '코스닥');
    }
    if(!html){
      html = '<pre style="white-space:pre-wrap;font-family:inherit;font-size:13px;color:#2d3436;line-height:1.6;margin:0;">'+
        _cpRaw.replace(/</g,'&lt;')+'</pre>';
    }
    el.innerHTML = html;
    return;
  }
  const sec = parseSection(_cpRaw, CP_SECTIONS[key]||[]);
  if(!sec){ el.innerHTML = '<span class="content-empty">해당 섹션 없음</span>'; return; }
  let html = '';
  if(key==='indicator'){
    html = _renderIndicatorCards(_parseIndicatorCards(sec));
  } else if(key==='us_market'){
    html = _renderTextBlock(sec);
  } else if(key==='signal'){
    html = _renderSignalCards(_parseSectorCards(sec));
  } else if(key==='sector'){
    html = _renderSectorCards(_parseSectorCards(sec));
  } else if(key==='kospi'){
    html = _renderStockCards(_parseStockCards(sec), '코스피');
  } else if(key==='kosdaq'){
    html = _renderStockCards(_parseStockCards(sec), '코스닥');
  }
  if(!html || html.includes('content-empty')){
    html = '<pre style="white-space:pre-wrap;font-family:inherit;font-size:13px;color:#2d3436;line-height:1.6;margin:0;">'+
      sec.replace(/</g,'&lt;')+'</pre>';
  }
  el.innerHTML = html;
}

function _parseGenericCards(text){
  const cards = [];
  const lines = text.split('\n');
  let cur = null;
  for(const raw of lines){
    const l = raw.trim();
    if(!l) continue;
    if(/^(📌|📊|🇺🇸|📡)/.test(l)) continue;
    if(l.startsWith('✔️')||l.startsWith('✔')||l.startsWith('✓')||l.startsWith('☑️')||l.startsWith('☑')){
      if(cur) cards.push(cur);
      cur = {name: l.replace(/^[✔️✔✓☑️☑]\s*/,'').trim(), bullets:[]};
    } else if(l.startsWith('-')||l.startsWith('•')){
      if(!cur) cur = {name:'',bullets:[]};
      cur.bullets.push(l.replace(/^[-•]\s*/,''));
    } else {
      if(cur) cards.push(cur);
      cur = {name: l, bullets:[]};
    }
  }
  if(cur) cards.push(cur);
  return cards.filter(c=>c.name||c.bullets.length);
}

function _renderGenericCards(cards, label){
  if(!cards.length) return '<span class="content-empty">정보가 없어요</span>';
  return '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-top:8px;">' +
    cards.map(c=>{
      const nameHtml = c.name ? _formatStockLine(c.name) : '';
      let html = '<div style="background:#fff;border:1px solid #e8b84b33;border-radius:10px;padding:12px 14px;">';
      if(label){
        html += '<div style="font-size:11px;color:#7a8099;font-weight:600;margin-bottom:4px;">'+label+'</div>';
      }
      if(nameHtml){
        html += '<div style="font-size:13px;font-weight:600;color:#1a1d23;margin-bottom:6px;">'+nameHtml+'</div>';
      }
      if(c.bullets.length){
        html += '<div style="font-size:12px;color:#2d3436;line-height:1.6;">'+c.bullets.map(b=>'• '+_formatStockLine(b)).join('<br>')+'</div>';
      }
      html += '</div>';
      return html;
    }).join('') + '</div>';
}

function _parseMarkerStockCards(text){
  const cards = [];
  const lines = text.split('\n');
  for(const raw of lines){
    const l = raw.trim();
    if(!l) continue;
    if(/^(📌|📊|🇺🇸|📡)/.test(l)) continue;
    let line = l.replace(/^[-•]\s*/,'');
    const m = line.match(/^(.+?)\s*[\(]?\s*([+-]?\d+\.?\d*%)\s*[\)]?\s*[:：]\s*(.+)$/);
    if(m){
      const [, name, pct, reason] = m;
      cards.push({name: name.trim(), pct: pct.trim(), reason: reason.trim()});
    } else {
      cards.push({name: line, pct: '', reason: ''});
    }
  }
  return cards;
}

function _renderMarkerStockCards(cards){
  if(!cards.length) return '<span class="content-empty">특징주 정보가 없어요</span>';
  return '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin-top:8px;">' +
    cards.map(c=>{
      const color = _pctColor(c.pct);
      let html = '<div style="background:#fff;border:1px solid #e8b84b33;border-radius:10px;padding:12px 14px;">';
      html += '<div style="font-size:11px;color:#7a8099;font-weight:600;margin-bottom:4px;">특징주</div>';
      html += '<div style="font-size:14px;font-weight:600;color:#1a1d23;margin-bottom:6px;">'+c.name;
      if(c.pct) html += ' <span style="color:'+color+';font-size:12px;font-weight:500;">'+c.pct+'</span>';
      html += '</div>';
      if(c.reason){
        html += '<div style="font-size:12px;color:#2d3436;line-height:1.6;">'+c.reason+'</div>';
      }
      html += '</div>';
      return html;
    }).join('') + '</div>';
}

function clTab(btn, key){
  document.querySelectorAll('#cl-tabs .tab').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  const el = document.getElementById('closing-body');
  if(!_clRaw){ return; }
  if(key==='all'){
    let html = '';
    const labels = {figure:'마감수치',factor:'지수 팩터',supply:'수급',sector:'특징 업종',stock:'특징주',schedule:'내일 일정'};
    for(const k of ['figure','factor','supply','sector','stock','schedule']){
      const sec = parseSection(_clRaw, CL_SECTIONS[k]||[]);
      if(sec){
        html += '<div style="font-size:11px;color:#7a8099;font-weight:700;letter-spacing:.08em;margin:'+(html?'14px':'6px')+' 0 4px;">📌 '+labels[k]+'</div>';
        if(k==='sector') html += _renderGenericCards(_parseGenericCards(sec), '특징 업종');
        else if(k==='stock') html += _renderMarkerStockCards(_parseMarkerStockCards(sec));
        else html += _renderGenericCards(_parseGenericCards(sec), '');
      }
    }
    if(!html){
      html = '<pre style="white-space:pre-wrap;font-family:inherit;font-size:13px;color:#2d3436;line-height:1.6;margin:0;">'+
        _clRaw.replace(/</g,'&lt;')+'</pre>';
    }
    el.innerHTML = html;
    return;
  }
  const sec = parseSection(_clRaw, CL_SECTIONS[key]||[]);
  if(!sec){ el.innerHTML = '<span class="content-empty">해당 섹션 없음</span>'; return; }
  let html = '';
  if(key==='sector'){
    html = _renderGenericCards(_parseGenericCards(sec), '특징 업종');
  } else if(key==='stock'){
    html = _renderMarkerStockCards(_parseMarkerStockCards(sec));
  } else {
    html = _renderGenericCards(_parseGenericCards(sec), '');
  }
  if(!html || html.includes('content-empty')){
    html = '<pre style="white-space:pre-wrap;font-family:inherit;font-size:13px;color:#2d3436;line-height:1.6;margin:0;">'+
      sec.replace(/</g,'&lt;')+'</pre>';
  }
  el.innerHTML = html;
}

function mapTab(btn, key){
  document.querySelectorAll('#tab-map-kospi,#tab-map-kosdaq').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  const isKospi = key==='kospi';
  document.getElementById('map-frame').src = isKospi
    ? 'https://markets.hankyung.com/marketmap/kospi'
    : 'https://markets.hankyung.com/marketmap/kosdaq';
  document.getElementById('map-link-kospi').style.display = isKospi ? 'inline-flex' : 'none';
  document.getElementById('map-link-kosdaq').style.display = isKospi ? 'none' : 'inline-flex';
}

function sectorTab(btn, key){
  document.querySelectorAll('#tab-sector-top,#tab-sector-bot').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('sector-top').style.display = key==='top' ? 'block' : 'none';
  document.getElementById('sector-bot').style.display = key==='bot' ? 'block' : 'none';
}

async function loadNews(){
  const el = document.getElementById('news-list');
  if(!el) return;
  el.innerHTML = '<span class="content-empty">로딩 중...</span>';
  try{
    const d = await fetch('/api/news').then(r=>r.json());
    if(!d.items || !d.items.length){
      el.innerHTML='<span class="content-empty">뉴스를 불러오지 못했어요.</span>';
      return;
    }
    el.innerHTML = d.items.map(item=>`
      <div style="padding:8px 0;border-bottom:1px solid #f0f2f5;">
        <div style="font-size:10px;color:#b2bec3;margin-bottom:3px;">${item.source}</div>
        <a href="${item.link}" target="_blank" style="font-size:12px;color:#2d3436;line-height:1.5;text-decoration:none;font-weight:500;">${item.title}</a>
      </div>`).join('');
  }catch(e){
    el.innerHTML='<span class="content-empty">오류가 발생했어요.</span>';
  }
}

async function loadAll(){
  const btn=document.getElementById('refresh-btn');
  if(btn)btn.innerHTML='<span class="spinner"></span>';
  await Promise.all([loadMarket(),loadSector()]);
  if(btn)btn.innerHTML='<span>↻</span> 새로고침';
}

// ── 체크포인트 인라인 편집 ─────────────────────────────
let _cpLinkMap = [];
let _cpEditing = false;  // 편집 모드 플래그 — polling이 편집 화면 덮지 않게

function cpRawToEdit(raw) {
  _cpLinkMap = [];
  return raw.replace(/\[\[LINK:([^\]]+)\]\]/g, (_, url) => {
    _cpLinkMap.push(url);
    return '[🔗' + _cpLinkMap.length + ']';
  });
}

function cpEditToRaw(editText) {
  let result = editText;
  _cpLinkMap.forEach((url, i) => {
    result = result.split('[🔗' + (i+1) + ']').join('[[LINK:' + url + ']]');
  });
  return result;
}

function enterCpEdit() {
  _cpEditing = true;
  const editText = cpRawToEdit(_cpRaw || '');
  const body = document.getElementById('checkpoint-body');
  const tabs = document.getElementById('cp-tabs');
  const editBtn = document.getElementById('cp-edit-btn');
  if(tabs) tabs.style.display = 'none';
  if(editBtn) editBtn.style.display = 'none';

  let legendHtml = '';
  if(_cpLinkMap.length > 0){
    legendHtml = '<div style="margin-bottom:10px;padding:10px 12px;background:#fff3a0;border-radius:8px;font-size:11px;line-height:1.7;">';
    legendHtml += '<div style="font-weight:700;margin-bottom:6px;">🔗 링크 토큰 — 지우면 링크 사라져요!</div>';
    _cpLinkMap.forEach((url, i) => {
      const short = url.length > 55 ? url.slice(0,55)+'…' : url;
      legendHtml += '<div style="display:flex;align-items:center;gap:6px;margin-bottom:2px;">'
        + '<b style="background:#1a1d23;color:#e8b84b;padding:1px 6px;border-radius:3px;font-size:11px;white-space:nowrap;">[🔗'+(i+1)+']</b>'
        + '<a href="'+url+'" target="_blank" style="color:#0984e3;font-size:10px;word-break:break-all;">'+short+'</a>'
        + '</div>';
    });
    legendHtml += '</div>';
  }

  const ta = document.createElement('textarea');
  ta.id = 'cp-edit-textarea';
  ta.style.cssText = 'width:100%;min-height:420px;font-family:monospace;font-size:13px;padding:12px;border:1.5px solid #e8b84b;border-radius:8px;background:#fdfbf5;resize:vertical;line-height:1.65;display:block;';
  ta.value = editText;

  const btnRow = document.createElement('div');
  btnRow.style.cssText = 'margin-top:10px;display:flex;gap:8px;align-items:center;';
  btnRow.innerHTML = '<button class="btn btn-green" onclick="saveCpEdit()">💾 저장</button>'
    + '<button class="btn" onclick="cancelCpEdit()">✕ 취소</button>'
    + '<span id="cp-edit-badge" style="font-size:11px;color:#00b894;font-weight:700;display:none;">✓ 저장됨</span>';

  body.innerHTML = legendHtml;
  body.appendChild(ta);
  body.appendChild(btnRow);
  ta.focus();
}

async function saveCpEdit() {
  const ta = document.getElementById('cp-edit-textarea');
  if(!ta) { alert('❌ textarea 못 찾음'); return; }
  const rawText = cpEditToRaw(ta.value);
  const url = '/api/post/checkpoint/replace';
  const payload = {content: rawText, date: new Date().toISOString().slice(0,10)};
  try{
    const res = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    let bodyTxt = '';
    try { bodyTxt = await res.text(); } catch(_){}
    if(!res.ok){
      alert('❌ 저장 실패 HTTP ' + res.status + '\n응답: ' + bodyTxt.slice(0, 300));
      return;
    }
    // 성공 — 강제 새로고침
    window.location.reload();
  } catch(e) {
    alert('❌ 네트워크/JS 오류: ' + (e && e.message ? e.message : e));
  }
}

function cancelCpEdit() {
  _cpEditing = false;
  const tabs = document.getElementById('cp-tabs');
  const editBtn = document.getElementById('cp-edit-btn');
  if(tabs) tabs.style.display = '';
  if(editBtn) editBtn.style.display = '';
  const activeBtn = document.querySelector('#cp-tabs .tab.active') || document.querySelector('#cp-tabs .tab');
  if(activeBtn){
    const m = activeBtn.getAttribute('onclick')?.match(/cpTab\(this,'(\w+)'\)/);
    if(m) cpTab(activeBtn, m[1]);
  }
}

// ── 초기화 (마인드맵식 — 다 비우기) ──────────────────
async function clearCheckpoint() {
  if(!confirm('체크포인트를 전부 비울까요?\n되돌릴 수 없어요. (텔레봇 last_checkpoint도 다음 메시지부터 새로 시작)')) return;
  try {
    const res = await fetch('/api/post/checkpoint/replace', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({content: '', date: new Date().toISOString().slice(0,10)})
    });
    if(!res.ok){ alert('초기화 실패 HTTP ' + res.status); return; }
    window.location.reload();
  } catch(e) {
    alert('초기화 실패: ' + e.message);
  }
}

async function clearClosing() {
  if(!confirm('마감일지를 전부 비울까요?\n되돌릴 수 없어요.')) return;
  try {
    const res = await fetch('/api/post/closing', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({content: '', date: new Date().toISOString().slice(0,10)})
    });
    if(!res.ok){ alert('초기화 실패 HTTP ' + res.status); return; }
    window.location.reload();
  } catch(e) {
    alert('초기화 실패: ' + e.message);
  }
}

loadAll();
loadFutures();
loadAutoFutures();
loadReport();
loadTodo();
loadMemo();
loadNews();
loadCalendar();
loadPDFList();
loadPost('checkpoint','checkpoint-body','checkpoint-date');
loadPost('closing','closing-body','closing-date');
loadPost('briefing','briefing-body','briefing-date');
setInterval(loadAll,5*60*1000);
// 체크포인트/마감일지/브리핑 자동 polling 제거 — 카드 헤더의 ↻ 새로고침 버튼으로 수동 갱신
</script>
<script>
if('serviceWorker' in navigator){
  navigator.serviceWorker.register('/service-worker.js');
}
</script>
</body>
</html>"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
