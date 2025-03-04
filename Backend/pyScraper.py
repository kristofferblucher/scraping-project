import shutil
from threading import Event
from flask import Flask, request, send_from_directory, render_template_string, render_template
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup
import pandas as pd
import datetime
import re
import os
import numpy as np

app = Flask(__name__)
stop_event = Event()

def get_full_urls(url):
    """Henter alle auksjons-URLer fra en gitt side."""
    response = requests.get(url)
    html = response.text
    soup = BeautifulSoup(html, 'html.parser')
    events = soup.find_all('div', {'class': 'card-body event-row'})
    return [f"https://auksjon.oslomyntgalleri.no{event.find('a')['href']}" for event in events if event.find('a')]


def get_auction_item_urls(auction_url, max_items_per_auction=None):
    """Henter alle objekt-URLer fra alle sider av en spesifikk auksjonsside."""
    item_urls = []
    current_page = 0
    while auction_url:
        if stop_event.is_set():
            return None

        response = requests.get(auction_url)
        html = response.text
        soup = BeautifulSoup(html, 'html.parser')
        gallery_units = soup.find_all('div', class_='galleryUnit')
        item_urls.extend(['https://auksjon.oslomyntgalleri.no' + unit.find('a')['href'] for unit in gallery_units if unit.find('a')])
        
        # Begrens antall objekter per auksjon hvis satt
        if max_items_per_auction is not None and len(item_urls) >= max_items_per_auction:
            item_urls = item_urls[:max_items_per_auction]
            break
        
        # Søker etter neste side-link basert på nummerering
        pagination_links = soup.find_all('a', href=True)
        next_page_link = None
        for link in pagination_links:
            if 'page=' + str(current_page + 1) in link['href']:
                next_page_link = link
                break

        if next_page_link:
            auction_url = 'https://auksjon.oslomyntgalleri.no' + next_page_link['href']
            current_page += 1
            print(f"Moving to next page: {auction_url}")
        else:
            print("No more pages found.")
            auction_url = None

    return item_urls


def get_auction_item_data(item_url):

    if stop_event.is_set():
        return None

    """Henter detaljert informasjon om et auksjonsobjekt."""
    response = requests.get(item_url)
    html = response.text
    soup = BeautifulSoup(html, 'html.parser')
    
    item_data = {}
    full_title = soup.find('h1', class_='detail__title').text.strip() if soup.find('h1', class_='detail__title') else ''
    
    # Endret regex for å inkludere alt etter "kroner"
    match = re.search(r'(\d+\s*kroner)(.*)', full_title, re.IGNORECASE)
    if match:
        item_data['Objekt'] = match.group(1).strip()
        item_data['År'] = match.group(2).strip()
    else:
        match = re.search(r'\b(\d{4})\b', full_title)
        if match:
            year_index = match.start(0)
            item_data['Objekt'] = full_title[:year_index].strip()
            item_data['År'] = full_title[year_index:].strip()
        else:
            item_data['Objekt'] = full_title
            item_data['År'] = ''

    # Tillegg for å hente ut subtitle informasjon om "Konge"
    subtitle = soup.find('span', class_='lead detail__subtitle')
    item_data['Konge'] = subtitle.text.strip() if subtitle else ''

    # Hent "Type" fra meta-tag 'keywords'
    keywords_meta = soup.find('meta', {'name': 'keywords'})
    item_data['Type'] = keywords_meta['content'] if keywords_meta else ''

    item_data['Vinnerbud'] = soup.find('span', class_='NumberPart').text.strip().replace('\xa0', '') if soup.find('span', class_='NumberPart') else ''

    strong_tags = soup.find_all('strong')
    for strong_tag in strong_tags:
        if 'objekt' in strong_tag.text.lower():
            item_data['Objekt nr'] = strong_tag.text.replace('Objektnr.', '').strip()
            break  # Avslutter loopen når vi finner første match
    
    
    auction_info = soup.find('span', class_='h5').text.strip() if soup.find('span', class_='h5') else ''
    item_data['Auksjonshus + auksjonsnummer'] = auction_info

    # Extract custom fields data
    custom_fields = soup.find_all('div', class_='detail__custom-fields')
    for field in custom_fields:
        field_name = field.find('span', class_='detail__field-name').text.strip().replace(':', '') if field.find('span', class_='detail__field-name') else ''
        field_value = field.find('span', class_='detail__field-value').text.strip() if field.find('span', class_='detail__field-value') else ''
        if field_name and field_value:
            item_data[field_name] = field_value

    # Calculate winning bid + premium (20% addition)
    if item_data['Vinnerbud']:
        try:
            vinnerbud = float(item_data['Vinnerbud'].replace('.', '').replace(',', '.'))
            item_data['Vinnerbud + salær'] = f"{vinnerbud * 1.2:,.2f}".replace(',', ' ').replace('.', ',')
        except ValueError:
            item_data['Vinnerbud + salær'] = ''
    else:
        item_data['Vinnerbud + salær'] = ''

    return item_data




from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup
import pandas as pd
import datetime

# Finn mappen der denne .py-filen ligger
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Sett en variabel for mappen der Excel-filene skal ligge
OUTPUT_DIR = os.path.join(BASE_DIR, "excel_exports")

# Opprett mappen om den ikke finnes
os.makedirs(OUTPUT_DIR, exist_ok=True)



def main(max_auctions=None, max_items_per_auction=None, search_term=None, search_term_year=None):
    global stop_event
    stop_event.clear()
    if stop_event.is_set():
        return None

    base_url = 'https://auksjon.oslomyntgalleri.no/Events'
    page = 1
    all_auction_urls = []

    while True:
        url = f"{base_url}?page={page}"
        new_urls = get_full_urls(url)
        if not new_urls:
            break
        all_auction_urls.extend(new_urls)
        if max_auctions is not None and len(all_auction_urls) >= max_auctions:
            all_auction_urls = all_auction_urls[:max_auctions]
            break
        page += 1

    item_data_list = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_url = {
            executor.submit(get_auction_item_data, item_url): item_url
            for auction_url in all_auction_urls
            for item_url in get_auction_item_urls(auction_url, max_items_per_auction)
        }

        for future in as_completed(future_to_url):
            if stop_event.is_set():
                executor.shutdown(wait=False)  # Avbryter pågående tasks
                break

            item_data = future.result()
            if search_term or search_term_year:
                objekt_navn = item_data.get("Objekt", "").lower()
                aar = item_data.get("År", "").lower()
                if search_term and search_term.lower() not in objekt_navn:
                    continue
                if search_term_year and search_term_year.lower() not in aar:
                    continue

            item_data_list.append(item_data)

    # **Hvis ingen objekter matchet søket, returner en tom fil**
    if not item_data_list:
        print("⚠️ Ingen objekter matchet søket.")
        return None

    df = pd.DataFrame(item_data_list)

    columns = [
        'Objekt', 'År', 'Vinnerbud', 'Vinnerbud + salær', 'Type', 'Objekt nr', 
        'Land', 'Utgave (for sedler), Pregested (for mynter)', 'Referanse', 
        'Referanse 2', 'Referanse 3', 'Referanse 4', 'Auksjonshus + auksjonsnummer', 
        'Info/kommentar/provinens', 'Proveniens', 'Konge'
    ]
    columns = [col for col in columns if col in df.columns]
    df = df[columns]

    df = df.replace('', np.nan)
    df = df.dropna(how='all', axis=0)
    df.reset_index(drop=True, inplace=True)

    
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H-%M-%S")
    filename = f"auksjonsdata_{timestamp}.xlsx"
    latest_filename = "auksjonsdata_latest.xlsx"

    
    full_path = os.path.join(OUTPUT_DIR, filename)
    latest_path = os.path.join(OUTPUT_DIR, latest_filename)

    df.to_excel(full_path, index=False)
    shutil.copy(full_path, latest_path)

    print(f"📁 Data eksportert til {filename}")
    return latest_path



def log_user_activity(activity):
    with open('scraping_log.txt', 'a') as file:
        file.write(f"{datetime.datetime.now().isoformat()}: {activity}\n")


scraping_active = False
#Flask Logikk: 


scraping_active = False  # Global variabel for å spore scraping-status

@app.route('/', methods=['GET', 'POST'])
def index():
    global scraping_active

    if request.method == 'POST':
        if scraping_active:
            return "Scraping is already in progress. Please wait or stop the current process.", 400
        
        print("Received POST request")  
        scraping_active = True  # Marker at scraping er i gang

        max_auctions = request.form.get('max_auctions')
        max_items_per_auction = request.form.get('max_items_per_auction')
        search_term = request.form.get('search_term')
        search_term_year = request.form.get('search_term_year')

        print(f"Captured search_term: '{search_term}'")

        activity = f"Scraped max_auctions={max_auctions}, max_items_per_auction={max_items_per_auction}, search_term={search_term}, search_term_year={search_term_year}"
        log_user_activity(activity)

        max_auctions = int(max_auctions) if max_auctions and max_auctions.isdigit() else None
        max_items_per_auction = int(max_items_per_auction) if max_items_per_auction and max_items_per_auction.isdigit() else None
        search_term = search_term.strip().lower() if search_term else None
        search_term_year = search_term_year.strip().lower() if search_term_year else None

        try:
            print("Calling main()...")  
            file_path = main(max_auctions, max_items_per_auction, search_term, search_term_year)
            print(f"File generated: {file_path}")  
            print("Rendering success.html...")  
            scraping_active = False  # Scraping er ferdig
            return render_template('success.html', file_path=file_path, scraping_active=scraping_active)
        except Exception as e:
            scraping_active = False  # Sett status til inaktiv hvis det feiler
            print(f"Error in main(): {e}")
            return str(e), 500  

    return render_template('index.html', scraping_active=scraping_active)





BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@app.route('/download-latest')
def download_latest():
    filename = 'auksjonsdata_latest.xlsx'
    full_path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(full_path):
        return "No file found. Please run the scraper first.", 404

    return send_from_directory(
        directory=OUTPUT_DIR, 
        path=filename, 
        as_attachment=True
    )


@app.route('/view-latest-data')
def view_latest_data():
    filename = "auksjonsdata_latest.xlsx"
    if not os.path.exists(filename):
        return "No data available. Please run the scraper first.", 404
    df = pd.read_excel(filename)
    return df.to_html()

@app.route('/stop', methods=['POST'])
def stop_scraping():
    global scraping_active
    stop_event.set()  # Aktiverer stop-hendelsen
    print("Scraping requested to stop.")
    scraping_active = False
    return render_template('index.html', message="Scraping has been stopped.", scraping_active=scraping_active)

@app.route('/scraping-log')
def view_scraping_log():
    log_file = 'scraping_log.txt'
    
    # Sjekk om filen eksisterer før du prøver å lese den
    if not os.path.exists(log_file):
        log_entries = []  # Hvis filen ikke finnes, bruk en tom liste
    else:
        with open(log_file, 'r') as file:
            log_entries = file.readlines()

    return render_template('scraping_log.html', log_entries=log_entries)


if __name__ == "__main__":
    app.run(debug=True)
