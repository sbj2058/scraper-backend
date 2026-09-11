import os
import re
import time
import pandas as pd
from flask import Flask, request, jsonify
from flask_cors import CORS
from playwright.sync_api import sync_playwright

app = Flask(__name__)
CORS(app)

DATA_STORE = []

def create_authenticated_context(browser, auth_token):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    if auth_token:
        if auth_token.lower().startswith("bearer ") or auth_token.startswith("ey"):
            headers["Authorization"] = auth_token if auth_token.lower().startswith("bearer ") else f"Bearer {auth_token}"
        else:
            headers["Cookie"] = auth_token

    return browser.new_context(extra_http_headers=headers)

def parse_text_block(category_name, text):
    records = []
    pattern = re.compile(
        r'((?:IGN|UID)[:\s][^\n]+)\n([^\n]+)\n(\d+\s+(?:minutes|hours|days|seconds)\s+ago)',
        re.IGNORECASE
    )
    matches = pattern.findall(text)
    for m in matches:
        records.append({
            "Category": category_name,
            "IGN_UID": m[0].strip(),
            "Username": m[1].strip(),
            "Time": m[2].strip()
        })
    return records

@app.route('/')
@app.route('/health')
def health_check():
    return jsonify({"status": "healthy", "service": "Playwright Scraper Engine"}), 200

@app.route('/api/fetch-categories', methods=['POST'])
def fetch_categories():
    data = request.json or {}
    url = data.get('url', '').strip()
    auth_token = data.get('auth_token', '').strip()

    if not url:
        return jsonify({"success": False, "error": "URL parameter is required"}), 400

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            context = create_authenticated_context(browser, auth_token)
            page = context.new_page()
            
            page.goto(url, timeout=30000, wait_until="networkidle")
            time.sleep(3)

            tab_candidates = page.locator("""
                aside button, aside a, aside li, aside [role="button"], aside [role="tab"],
                div[class*="sidebar"] button, div[class*="sidebar"] a, div[class*="sidebar"] li,
                div[class*="sidebar"] [role="button"], div[class*="sidebar"] [role="tab"],
                div[class*="nav"] button, div[class*="nav"] a, div[class*="menu"] div,
                button[class*="tab"], div[class*="tab"]
            """).all()

            cats = []
            for el in tab_candidates:
                try:
                    raw_text = el.inner_text().strip()
                    if not raw_text:
                        continue
                    lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
                    if not lines:
                        continue
                    title = lines[0]

                    if (
                        len(title) > 2
                        and not re.search(r'^\d+\s*requests?$', title, re.IGNORECASE)
                        and title not in cats
                    ):
                        cats.append(title)
                except Exception:
                    continue

            browser.close()

        return jsonify({"success": True, "categories": cats})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/scrape', methods=['POST'])
def scrape():
    global DATA_STORE
    data = request.json or {}
    url = data.get('url', '').strip()
    selected_cats = data.get('categories', [])
    max_pages = int(data.get('max_pages', 3))
    auth_token = data.get('auth_token', '').strip()

    if not url or not selected_cats:
        return jsonify({"success": False, "error": "URL and selected categories are required"}), 400

    all_records = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            context = create_authenticated_context(browser, auth_token)
            page = context.new_page()

            page.goto(url, timeout=30000, wait_until="networkidle")
            time.sleep(3)

            for cat in selected_cats:
                escaped_cat = re.escape(cat)
                tab_locator = page.locator(f"text=/{escaped_cat}/i").first

                if tab_locator.count() > 0:
                    try:
                        tab_locator.scroll_into_view_if_needed()
                        tab_locator.click()
                        time.sleep(1.5)
                    except Exception as click_err:
                        print(f"Could not click tab '{cat}': {click_err}")

                for _ in range(max_pages):
                    main_panel = page.locator("main, div[class*='content'], div:has(> button:has-text('>'))").first
                    page_text = main_panel.inner_text() if main_panel.count() > 0 else page.inner_text("body")
                    
                    records = parse_text_block(cat, page_text)
                    all_records.extend(records)

                    next_btn = page.locator("button:has-text('>'), a:has-text('>'), [aria-label*='Next']").first
                    if next_btn.count() > 0 and next_btn.is_enabled() and next_btn.is_visible():
                        next_btn.click()
                        time.sleep(1.2)
                    else:
                        break

            browser.close()

        DATA_STORE = all_records
        return jsonify({"success": True, "data": DATA_STORE, "count": len(DATA_STORE)})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)