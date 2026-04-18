import json
import os
import re
import urllib.request
import urllib.parse
import gspread
from google.oauth2.service_account import Credentials

# --- CONFIG ---
API_URL = "https://ai4trade.ai/api/signals/feed?message_type=discussion&limit=20&offset=0&sort=new"
SPREADSHEET_ID = "1x1YiJdk45GZHytiSx5MRE96Yu7BEBUm7AXsZMmVVyBM"
SHEET_NAME = "discuss"

def get_data():
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
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
                    s.get('created_at', '')
                ])
            return results
    except Exception as e:
        print(f"Error ambil data: {e}")
        return []

def update_sheets(values):
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds_json = os.getenv("G_SHEETS_CREDS")
        if creds_json:
            info = json.loads(creds_json)
            creds = Credentials.from_service_account_info(info, scopes=scopes)
            gc = gspread.authorize(creds)
            sh = gc.open_by_key(SPREADSHEET_ID)
            worksheet = sh.worksheet(SHEET_NAME)
            
            worksheet.clear()
            headers = ["Title", "Author", "Content", "Timestamp"]
            worksheet.update(range_name="A1", values=[headers] + values)
            print(f"Berhasil update {len(values)} baris!")
    except Exception as e:
        print(f"Error update sheets: {e}")

if __name__ == "__main__":
    data_points = get_data()
    print(f"Ditemukan {len(data_points)} data.")
    if data_points:
        update_sheets(data_points)
