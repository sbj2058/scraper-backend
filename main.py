import os
import re
import time
import pandas as pd
from flask import Flask, request, jsonify
from flask_cors import CORS
from playwright.sync_api import sync_playwright

# 1. Instantiate Flask application (Explicitly named 'app' for Gunicorn)
app = Flask(__name__)

# 2. Enable CORS to allow requests from your GitHub Pages frontend
CORS(app)

DATA_STORE = []

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
    """Health check endpoint for Render monitoring."""
    return jsonify({"status": "healthy", "service": "Playwright Scraper Engine"}), 200

@app.route('/api/fetch-categories', methods=['POST'])
def fetch_categories():
    data = request.json or {}
    url = data.get('url', '').strip()

    if not url:
        return jsonify({"success": False, "error": "URL parameter is missing"}), 400

    try:
        with sync_playwright() as p:
            # Launch Chromium with headless flags optimized for container environments
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            page = browser.new_page()
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            time.sleep(2)

            tab_candidates = page.locator("""
                aside button, aside a, aside li, aside [role="button"], aside [role="tab"],
                div[class*="sidebar"] button, div[class*="sidebar"] a, div[class*="sidebar"] li,
                div[class*="sidebar"] [role="button"], div[class*="sidebar"] [role="tab"],
                div[class*="nav"] button, div[class*="nav"] a, div[class*="menu"] div
            """).all()

            if not tab_candidates:
                tab_candidates = page.locator("aside > div, div[class*='sidebar'] > div, ul > li").all()

            cats = []
            for el in tab_candidates:
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

    if not url or not selected_cats:
        return jsonify({"success": False, "error": "URL and categories are required"}), 400

    all_records = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            page = browser.new_page()
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            time.sleep(2)

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