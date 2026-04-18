import csv
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import List, Set, Tuple

import gspread
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By


from selenium.webdriver.support.ui import WebDriverWait


BASE_URL = "https://ai4trade.ai/strategies"
API_BASE_URL = "https://ai4trade.ai/api"
BACKUP_FILE = "strategies_backup.json"
SHEET_TEMPLATE_FILE = "strategies_sheet_template.csv"
SHEET_DATA_FILE = "strategies_sheet_data.csv"
SPREADSHEET_URL = os.getenv(
    "SPREADSHEET_URL",
    "https://docs.google.com/spreadsheets/d/1x1YiJdk45GZHytiSx5MRE96Yu7BEBUm7AXsZMmVVyBM/edit?gid=1973967455#gid=1973967455",
).strip()
SHEET_NAME = os.getenv("SHEET_NAME", "strategies").strip() or "strategies"
TARGET_GID = int(os.getenv("TARGET_GID", "1973967455"))
CREDENTIALS_FILE = (
    os.getenv("CREDENTIALS_FILE", "").strip()
    or os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    or "credentials.json"
)
WAIT_SECONDS = 15
SCROLL_ROUNDS = 5
SCROLL_PAUSE_SECONDS = 2
MAX_PAGES_TO_SCRAPE = 10
PAGE_SIZE = 20
STRATEGY_SCRAPE_MODE = os.getenv("STRATEGY_SCRAPE_MODE", "api").strip().lower()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://lab.hellobotkilat.my.id/webhook-test/discus").strip()

_spreadsheet_id_match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", SPREADSHEET_URL)
SPREADSHEET_ID = _spreadsheet_id_match.group(1) if _spreadsheet_id_match else ""


def build_driver() -> webdriver.Chrome:
    cache_path = os.path.abspath(".selenium-cache")
    os.makedirs(cache_path, exist_ok=True)
    os.environ["SE_CACHE_PATH"] = cache_path

    common_flags = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-software-rasterizer",
        "--window-size=1920,1080",
    ]
    launch_variants = [
        ["--headless=new", "--remote-debugging-port=9222"],
        ["--headless", "--remote-debugging-pipe", "--single-process", "--no-zygote"],
    ]

    last_error = None
    for variant_flags in launch_variants:
        options = Options()
        for flag in common_flags + variant_flags:
            options.add_argument(flag)
        options.add_argument(f"--user-data-dir={tempfile.mkdtemp(prefix='chrome-profile-')}")
        try:
            return webdriver.Chrome(options=options)
        except Exception as err:
            last_error = err
            print(f"Chrome start gagal dengan opsi {variant_flags}: {err}")

    if last_error:
        raise last_error
    raise RuntimeError("Gagal membuat Chrome driver.")


def wait_for_page_ready(driver: webdriver.Chrome) -> None:
    wait = WebDriverWait(driver, WAIT_SECONDS)
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")


def click_newest_tab(driver: webdriver.Chrome) -> bool:
    def is_newest_active() -> bool:
        try:
            return bool(
                driver.execute_script(
                    """
                    const buttons = Array.from(document.querySelectorAll('button.btn.btn-ghost'));
                    return buttons.some(btn => {
                      const label = (btn.innerText || '').trim().toLowerCase();
                      if (label !== 'newest') return false;
                      const style = (btn.getAttribute('style') || '').toLowerCase();
                      return style.includes('background: var(--accent-primary)');
                    });
                    """
                )
            )
        except Exception:
            return False

    if is_newest_active():
        return True

    newest_button = None
    selectors = [
        "//button[contains(@class,'btn') and contains(@class,'btn-ghost') and normalize-space()='Newest']",
        "//button[normalize-space()='Newest']",
    ]
    for selector in selectors:
        try:
            newest_button = driver.find_element(By.XPATH, selector)
            if newest_button:
                break
        except Exception:
            newest_button = None

    if newest_button is None:
        return False

    try:
        newest_button.click()
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", newest_button)
        except Exception:
            return False

    try:
        WebDriverWait(driver, WAIT_SECONDS).until(lambda d: is_newest_active())
    except Exception:
        pass
    return True


def scroll_down(driver: webdriver.Chrome, rounds: int = SCROLL_ROUNDS) -> None:
    for _ in range(rounds):
        try:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        except WebDriverException:
            pass
        time.sleep(SCROLL_PAUSE_SECONDS)


def first_text_from_element(card, selectors: List[str]) -> str:
    for selector in selectors:
        try:
            elements = card.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            continue

        for element in elements:
            try:
                text = element.text.strip()
            except Exception:
                continue
            if text:
                return text
    return ""


def find_active_timestamp(card) -> str:
    prefixes = [
        "active ",
        "recently active ",
        "terakhir aktif ",
        "最近活跃 ",
    ]

    try:
        card_text = card.text or ""
    except Exception:
        card_text = ""

    for line in card_text.splitlines():
        line_clean = line.strip()
        lowered = line_clean.lower()
        for prefix in prefixes:
            if lowered.startswith(prefix):
                return line_clean[len(prefix) :].strip()

        match = re.search(
            r"(\d{1,2}/\d{1,2}/\d{4},\s*\d{1,2}:\d{2}:\d{2}\s*[APMapm]{2})",
            line_clean,
        )
        if match:
            return match.group(1).strip()

    try:
        spans = card.find_elements(By.CSS_SELECTOR, "span, div")
    except Exception:
        spans = []

    for item in spans:
        try:
            text = item.text.strip()
        except Exception:
            continue
        lowered = text.lower()
        for prefix in prefixes:
            if lowered.startswith(prefix):
                return text[len(prefix) :].strip()

        match = re.search(
            r"(\d{1,2}/\d{1,2}/\d{4},\s*\d{1,2}:\d{2}:\d{2}\s*[APMapm]{2})",
            text,
        )
        if match:
            return match.group(1).strip()
    return ""


def scrape_discussion_cards(driver: webdriver.Chrome) -> List[dict]:
    try:
        WebDriverWait(driver, WAIT_SECONDS).until(
            lambda d: len(d.find_elements(By.CSS_SELECTOR, ".signal-card")) > 0
        )
    except TimeoutException:
        return []

    try:
        cards = driver.find_elements(By.CSS_SELECTOR, ".signal-card")
    except Exception:
        cards = []

    results: List[dict] = []
    for card in cards:
        try:
            title = first_text_from_element(card, [".signal-symbol"])
        except Exception:
            title = ""

        try:
            author = first_text_from_element(card, [".signal-meta-author", ".signal-author"])
            if not author:
                author = first_text_from_element(
                    card,
                    ["div[style*='font-size: 12px']"],
                )
        except Exception:
            author = ""

        try:
            content = first_text_from_element(card, [".signal-content"])
        except Exception:
            content = ""

        try:
            timestamp = find_active_timestamp(card)
        except Exception:
            timestamp = ""

        if title or content or timestamp:
            results.append(
                {
                    "title": title,
                    "author": author,
                    "content": content,
                    "timestamp": timestamp,
                }
            )

    return results


def get_page_marker(cards: List[dict]) -> str:
    if not cards:
        return ""
    first = cards[0]
    return f"{first.get('title','')}|{first.get('timestamp','')}|{first.get('author','')}"


def get_current_page_number(driver: webdriver.Chrome) -> int:
    try:
        cards = driver.find_elements(By.CSS_SELECTOR, "div.card")
    except Exception:
        cards = []

    for card in cards:
        try:
            card_text = (card.text or "").strip()
        except Exception:
            card_text = ""
        if not card_text:
            continue

        # Pagination biasanya berisi ratio halaman seperti 1 / 25.
        m = re.search(r"(\d+)\s*/\s*(\d+)", card_text)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                pass

    try:
        body_text = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
    except Exception:
        body_text = ""
    match = re.search(r"page\s*(\d+)\s*/\s*(\d+)", body_text, re.IGNORECASE)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except Exception:
        return 0


def click_next_page(driver: webdriver.Chrome, current_marker: str) -> bool:
    next_labels = ["next", "next page", "下一页", "berikutnya", "selanjutnya"]

    def is_next_label(label: str) -> bool:
        label_clean = (label or "").strip().lower()
        return any(token in label_clean for token in next_labels)

    def find_pagination_card():
        try:
            cards = driver.find_elements(By.CSS_SELECTOR, "div.card")
        except Exception:
            cards = []

        for card in cards:
            try:
                text = (card.text or "").strip()
            except Exception:
                text = ""
            if not text:
                continue

            lowered = text.lower()
            has_page_text = re.search(r"page\s+\d+\s*/\s*\d+", lowered) is not None
            has_total_text = "discussions total" in lowered
            if has_page_text or has_total_text:
                return card
        return None

    pagination_card = find_pagination_card()
    next_button = None
    try:
        if pagination_card is not None:
            buttons = pagination_card.find_elements(By.CSS_SELECTOR, "button.btn.btn-secondary")
        else:
            buttons = driver.find_elements(By.CSS_SELECTOR, "button.btn.btn-secondary")
    except Exception:
        buttons = []

    for button in buttons:
        try:
            label = (button.text or "").strip()
        except Exception:
            label = ""
        if is_next_label(label):
            next_button = button
            break

    if next_button is None:
        try:
            next_button = driver.execute_script(
                """
                const cards = Array.from(document.querySelectorAll('div.card'));
                let scoped = null;
                for (const card of cards) {
                  const txt = (card.innerText || '').toLowerCase();
                  if (/page\\s+\\d+\\s*\\/\\s*\\d+/.test(txt) || txt.includes('discussions total')) {
                    scoped = card;
                    break;
                  }
                }
                const root = scoped || document;
                const btns = Array.from(root.querySelectorAll('button.btn.btn-secondary'));
                return btns.find(b => {
                  const t = (b.innerText || '').trim().toLowerCase();
                  return t.includes('next') || t.includes('下一页') || t.includes('berikutnya') || t.includes('selanjutnya');
                }) || null;
                """
            )
        except Exception:
            next_button = None

    if next_button is None:
        try:
            next_button = driver.find_element(
                By.XPATH,
                "//button[contains(@class,'btn') and contains(@class,'btn-secondary') and (contains(translate(normalize-space(.), 'NEXT', 'next'), 'next') or contains(normalize-space(.), '下一页'))]",
            )
        except Exception:
            next_button = None

    if next_button is None:
        return False

    try:
        button_class = (next_button.get_attribute("class") or "").lower()
        is_enabled = next_button.is_enabled() and (
            next_button.get_attribute("disabled") is None
        )
    except Exception:
        button_class = ""
        is_enabled = True

    if (not is_enabled) or ("disabled" in button_class):
        return False

    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", next_button)
    except Exception:
        pass

    before_page_no = get_current_page_number(driver)

    try:
        next_button.click()
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", next_button)
        except Exception:
            return False

    try:
        WebDriverWait(driver, WAIT_SECONDS).until(lambda d: get_current_page_number(d) > before_page_no)
    except Exception:
        try:
            WebDriverWait(driver, WAIT_SECONDS).until(
                lambda d: get_page_marker(scrape_discussion_cards(d)) != current_marker
            )
        except Exception:
            time.sleep(2)

    return True


def scrape_all_pages(driver: webdriver.Chrome) -> List[dict]:
    all_results: List[dict] = []
    seen_keys: Set[Tuple[str, str, str, str]] = set()
    max_pages = MAX_PAGES_TO_SCRAPE

    for page_no in range(1, max_pages + 1):
        scroll_down(driver, SCROLL_ROUNDS)
        page_cards = scrape_discussion_cards(driver)
        marker = get_page_marker(page_cards)
        current_page_no = get_current_page_number(driver)
        page_label = current_page_no if current_page_no > 0 else page_no
        print(f"Halaman {page_label}: ditemukan {len(page_cards)} card diskusi")

        for item in page_cards:
            key = (
                item.get("title", ""),
                item.get("author", ""),
                item.get("content", ""),
                item.get("timestamp", ""),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_results.append(item)

        if not click_next_page(driver, marker):
            print(f"Pagination berhenti di halaman {page_no} (tombol Next tidak ada / tidak aktif).")
            break

        try:
            wait_for_page_ready(driver)
        except Exception:
            pass

    return all_results


def scrape_all_pages_via_api() -> List[dict]:
    all_results: List[dict] = []
    seen_keys: Set[Tuple[str, str, str, str]] = set()

    for page_no in range(1, MAX_PAGES_TO_SCRAPE + 1):
        offset = (page_no - 1) * PAGE_SIZE
        params = urllib.parse.urlencode(
            {
                "message_type": "strategy",
                "limit": PAGE_SIZE,
                "offset": offset,
                # Frontend tombol "Newest" menggunakan sort "new".
                "sort": "new",
            }
        )
        url = f"{API_BASE_URL}/signals/feed?{params}"
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
        )

        try:
            with urllib.request.urlopen(request, timeout=WAIT_SECONDS + 15) as response:
                raw = response.read().decode("utf-8", errors="replace")
                payload = json.loads(raw)
        except Exception as err:
            print(f"Gagal ambil API strategies halaman {page_no}: {err}")
            break

        signals = payload.get("signals") or []
        print(f"Halaman API {page_no}: ditemukan {len(signals)} strategy")

        for item in signals:
            strategy = {
                "title": str(item.get("title") or "").strip(),
                "author": str(item.get("agent_name") or "").strip(),
                "content": str(item.get("content") or "").strip(),
                "timestamp": str(
                    item.get("timestamp")
                    or item.get("created_at")
                    or item.get("last_reply_at")
                    or ""
                ).strip(),
            }
            key = (
                strategy["title"],
                strategy["author"],
                strategy["content"],
                strategy["timestamp"],
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_results.append(strategy)

        has_more = bool(payload.get("has_more"))
        if not has_more or not signals:
            break

    return all_results


def create_sheet_template() -> None:
    headers = ["title", "author", "content", "timestamp"]
    with open(SHEET_TEMPLATE_FILE, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
    print(f"Template sheet dibuat: {SHEET_TEMPLATE_FILE}")


def save_sheet_data(discussions: List[dict]) -> None:
    headers = ["title", "author", "content", "timestamp"]
    with open(SHEET_DATA_FILE, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        for row in discussions:
            safe_row = {key: row.get(key, "") for key in headers}
            writer.writerow(safe_row)
    print(f"Data sheet disimpan: {SHEET_DATA_FILE}")


def save_backup(payload: dict) -> None:
    with open(BACKUP_FILE, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    print(f"Backup disimpan ke file: {BACKUP_FILE}")


def write_to_google_sheets(
    discussions: List[dict], spreadsheet_id: str, sheet_name: str, target_gid: int
) -> bool:
    if not spreadsheet_id:
        print("SPREADSHEET_ID tidak valid. Cek SPREADSHEET_URL.")
        return False
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        credentials = Credentials.from_service_account_file(
            CREDENTIALS_FILE, scopes=scopes
        )
        gc = gspread.authorize(credentials)
        spreadsheet = gc.open_by_key(spreadsheet_id)
        
        worksheet = None
        if target_gid > 0:
            try:
                worksheet = spreadsheet.get_worksheet_by_id(target_gid)
                print(f"Menggunakan worksheet gid={target_gid}: '{worksheet.title}'.")
            except Exception as err:
                print(f"Gagal ambil worksheet by gid={target_gid}: {err}")

        if worksheet is None:
            try:
                worksheet = spreadsheet.worksheet(sheet_name)
                print(f"Menggunakan worksheet nama '{sheet_name}'.")
            except gspread.exceptions.WorksheetNotFound:
                print(
                    f"Worksheet '{sheet_name}' tidak ditemukan, dan gid={target_gid} juga gagal. "
                    "Sesuai aturan, script tidak akan membuat worksheet baru."
                )
                return False
            except Exception as err:
                print(f"Gagal ambil worksheet '{sheet_name}': {err}")
                return False
        
        worksheet.clear()

        headers = ["title", "author", "content", "timestamp"]
        values = [headers]
        for discussion in discussions:
            values.append(
                [
                    discussion.get("title", ""),
                    discussion.get("author", ""),
                    discussion.get("content", ""),
                    discussion.get("timestamp", ""),
                ]
            )

        worksheet.update(values=values, range_name="A1", value_input_option="RAW")
        
        print(f"Data berhasil ditulis ke Google Sheets: {spreadsheet_id}")
        return True
    except FileNotFoundError:
        print(f"File credential tidak ditemukan: {CREDENTIALS_FILE}")
        print("Simpan service-account key JSON sebagai credentials.json, atau set CREDENTIALS_FILE / GOOGLE_APPLICATION_CREDENTIALS.")
        return False
    except Exception as e:
        print(f"Gagal menulis ke Google Sheets: {e}")
        return False


def main() -> None:
    discussions: List[dict] = []
    mode = STRATEGY_SCRAPE_MODE

    if mode in ("api", "api_first"):
        discussions = scrape_all_pages_via_api()
        if discussions:
            print("Scraping strategy via API berhasil.")
        elif mode == "api":
            print("Scraping API tidak menghasilkan data.")

    if (not discussions) and mode in ("browser", "api_first"):
        print("Fallback ke scraping browser (Selenium).")
        driver = build_driver()
        try:
            driver.get(BASE_URL)
            wait_for_page_ready(driver)
            click_newest_tab(driver)
            discussions = scrape_all_pages(driver)
        finally:
            driver.quit()

    payload = {
        "source": "ai4trade_strategies",
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(discussions),
        "data": discussions,
    }

    print(f"Jumlah strategy berhasil diambil: {len(discussions)}")
    create_sheet_template()
    save_sheet_data(discussions)
    save_backup(payload)

    if write_to_google_sheets(discussions, SPREADSHEET_ID, SHEET_NAME, TARGET_GID):
        print("Data berhasil dikirim ke Google Sheets.")
    else:
        print("Gagal menulis ke Google Sheets.")


if __name__ == "__main__":
    main()
