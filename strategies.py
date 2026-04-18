import json
import os
import re
import urllib.request
import urllib.parse
import gspread
from google.oauth2.service_account import Credentials

# --- CONFIG ---
# Link API khusus untuk strategi
API_URL = "https://ai4trade.ai/api/signals/feed?message_type=strategy&limit=20&offset=0&sort=new"
SPREADSHEET_ID = "1x1YiJdk45GZHytiSx5MRE96Yu7BEBUm7AXsZMmVVyBM"
SHEET_NAME = "strategies"

def get_data():
    try:
        # Meniru browser agar tidak diblokir
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        req = urllib.request.Request(API_URL, headers=headers)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            signals = data.get('signals', [])
            results = []
            for s in signals:
                results.append([
                    s.get('title', ''),
                    s.get('agent_name', ''),
                    s.get('content', ''),
                    s.get('created_at', s.get('timestamp', ''))
                ])
            return results
    except Exception as e:
        print(f"Error ambil data strategies: {e}")
        return []

def update_sheets(values):
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        # Mengambil kredensial dari Secret GitHub
        creds_json = os.getenv("G_SHEETS_CREDS")
        if creds_json:
            info = json.loads(creds_json)
            creds = Credentials.from_service_account_info(info, scopes=scopes)
            gc = gspread.authorize(creds)
            sh = gc.open_by_key(SPREADSHEET_ID)
            
            # Memastikan worksheet ada
            try:
                worksheet = sh.worksheet(SHEET_NAME)
            except gspread.exceptions.WorksheetNotFound:
                print(f"Tab '{SHEET_NAME}' tidak ketemu di Sheets!")
                return
            
            # Bersihkan data lama dan masukkan data baru
            worksheet.clear()
            headers = ["Title", "Author", "Content", "Timestamp"]
            worksheet.update(range_name="A1", values=[headers] + values)
            print(f"Berhasil update {len(values)} baris ke tab strategies!")
        else:
            print("G_SHEETS_CREDS tidak ditemukan di Environment Variable.")
    except Exception as e:
        print(f"Error update sheets: {e}")

if __name__ == "__main__":
    print("Memulai scraping strategies via API...")
    data_points = get_data()
    print(f"Ditemukan {len(data_points)} data strategies.")
    if data_points:
        update_sheets(data_points)
    else:
        print("Tidak ada data untuk dikirim.")
