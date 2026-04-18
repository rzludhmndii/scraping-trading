import json
import os
import urllib.request
import gspread
from google.oauth2.service_account import Credentials
import time

# --- KONFIGURASI ---
SPREADSHEET_ID = "1x1YiJdk45GZHytiSx5MRE96Yu7BEBUm7AXsZMmVVyBM"
SHEET_NAME = "strategies"

def get_data():
    all_results = []
    # Loop untuk mengambil 5 halaman (5 x 40 = 200 data)
    for page in range(5):
        offset = page * 40
        url = f"https://ai4trade.ai/api/signals/feed?message_type=strategy&limit=40&offset={offset}&sort=new"
        
        try:
            print(f"Mengambil data halaman {page + 1}...")
            headers = {'User-Agent': 'Mozilla/5.0'}
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
                signals = data.get('signals', [])
                
                if not signals: # Berhenti jika sudah tidak ada data lagi
                    break
                    
                for s in signals:
                    all_results.append([
                        s.get('title', ''),
                        s.get('agent_name', ''),
                        s.get('content', '').replace('\n', ' '),
                        s.get('created_at', s.get('timestamp', ''))
                    ])
            # Jeda sebentar biar tidak dianggap spam oleh server
            time.sleep(1) 
        except Exception as e:
            print(f"Gagal di halaman {page + 1}: {e}")
            break
            
    return all_results

def write_to_sheets(values):
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds_json = os.getenv("G_SHEETS_CREDS")
        
        if not creds_json:
            print("Error: Secret G_SHEETS_CREDS tidak ditemukan!")
            return

        info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(info, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SPREADSHEET_ID)
        worksheet = sh.worksheet(SHEET_NAME)
        
        # Bersihkan data lama
        worksheet.clear()
        
        # Header kolom
        headers = ["Title", "Author", "Content", "Timestamp"]
        
        # Update ke Sheets
        worksheet.update(range_name="A1", values=[headers] + values)
        print(f"Berhasil! Total {len(values)} data strategi masuk ke Sheets.")
    except Exception as e:
        print(f"Gagal menulis ke Sheets: {e}")

if __name__ == "__main__":
    print("Memulai Scraping 200 Strategi...")
    data = get_data()
    print(f"Total data ditemukan: {len(data)}")
    if data:
        write_to_sheets(data)
