import json
import os
import urllib.request
import gspread
from google.oauth2.service_account import Credentials

# --- KONFIGURASI ---
SPREADSHEET_ID = "1x1YiJdk45GZHytiSx5MRE96Yu7BEBUm7AXsZMmVVyBM"
SHEET_NAME = "discuss"

def get_data():
    results = []
    # Ambil 3 halaman biar dapet banyak (60 data)
    for page in range(3):
        offset = page * 20
        url = f"https://ai4trade.ai/api/signals/feed?message_type=discussion&limit=20&offset={offset}&sort=new"
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
                signals = data.get('signals', [])
                for s in signals:
                    results.append([
                        s.get('title', ''),
                        s.get('agent_name', ''),
                        s.get('content', '').replace('\n', ' '), # Bersihkan enter
                        s.get('created_at', '')
                    ])
        except Exception as e:
            print(f"Gagal ambil halaman {page+1}: {e}")
    return results

def write_to_sheets(values):
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        # Cek apakah ada di GitHub Secrets
        creds_json = os.getenv("G_SHEETS_CREDS")
        
        if creds_json:
            info = json.loads(creds_json)
            creds = Credentials.from_service_account_info(info, scopes=scopes)
            print("Koneksi: Menggunakan GitHub Secrets.")
        else:
            # Jika di laptop
            creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
            print("Koneksi: Menggunakan file lokal.")

        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SPREADSHEET_ID)
        worksheet = sh.worksheet(SHEET_NAME)
        
        # Bersihkan dan Update
        worksheet.clear()
        headers = ["Title", "Author", "Content", "Timestamp"]
        worksheet.update(range_name="A1", values=[headers] + values)
        print(f"Sukses! {len(values)} data diskusi masuk ke Sheets.")
    except Exception as e:
        print(f"Gagal menulis ke Sheets: {e}")

if __name__ == "__main__":
    print("Memulai Scraping Diskusi...")
    data = get_data()
    print(f"Total ditemukan: {len(data)} diskusi.")
    if data:
        write_to_sheets(data)
