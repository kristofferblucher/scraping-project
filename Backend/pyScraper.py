import shutil
import threading
from flask import Flask, request, send_from_directory, render_template_string, render_template
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup
import pandas as pd
import datetime
import re
import os

app = Flask(__name__)

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
    item_data['Objekt nr'] = soup.find('strong').text.replace('Objektnr.', '').strip() if soup.find('strong') else ''
    auction_info = soup.find('span', class_='h5').text.strip() if soup.find('span', class_='h5') else ''
    item_data['Auksjonshus + auksjonsnummer'] = auction_info

    # Extract custom fields data
    custom_fields = soup.find_all('div', class_='detail__custom-fields')
    for field in custom_fields:
        field_name = field.find('span', class_='detail__field-name').text.strip().replace(':', '') if field.find('span', class_='detail__field-name') else ''
        field_value = field.find('span', class_='detail__field-value').text.strip() if field.find('span', class_='detail__field-value') else ''
        if field_name and field_value:
            item_data[field_name] = field_value
            print(f"Field Name: {field_name}, Field Value: {field_value}")

    # Calculate winning bid + premium (20% addition)
    if item_data['Vinnerbud']:
        try:
            vinnerbud = float(item_data['Vinnerbud'].replace('.', '').replace(',', '.'))
            item_data['Vinnerbud + salær'] = f"{vinnerbud * 1.2:,.2f}".replace(',', ' ').replace('.', ',')
            print(f"Vinnerbud: {vinnerbud}, Vinnerbud + salær: {item_data['Vinnerbud + salær']}")
        except ValueError:
            item_data['Vinnerbud + salær'] = ''
    else:
        item_data['Vinnerbud + salær'] = ''

    print(f"Hentet data for objekt: {item_data['Objekt']}")

    return item_data




from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup
import pandas as pd
import datetime




def main(max_auctions=None, max_items_per_auction=None):
    base_url = 'https://auksjon.oslomyntgalleri.no/Events'
    page = 1
    all_auction_urls = []

    while True:
        url = f"{base_url}?page={page}"
        new_urls = get_full_urls(url)
        if not new_urls:  # Stopper hvis det ikke er flere URL-er å hente
            break
        all_auction_urls.extend(new_urls)
        if max_auctions is not None and len(all_auction_urls) >= max_auctions:
            all_auction_urls = all_auction_urls[:max_auctions]
            break
        page += 1

    item_data_list = []
    # Bruk ThreadPoolExecutor for å parallellisere datainnhenting
    with ThreadPoolExecutor(max_workers=10) as executor:
        # Opprett en fremtidig til hver URL
        future_to_url = {executor.submit(get_auction_item_data, item_url): item_url for auction_url in all_auction_urls for item_url in get_auction_item_urls(auction_url, max_items_per_auction)}
        # Iterer gjennom fullførte futures og hent resultatene
        for future in as_completed(future_to_url):
            item_data = future.result()
            item_data_list.append(item_data)
            print(f"Hentet data for: {item_data['Objekt']}")

    df = pd.DataFrame(item_data_list)

    # Spesifiser ønsket kolonnerekkefølge med "Vinnerbud + salær" rett etter "Vinnerbud"
    columns = [
        'Objekt', 'År', 'Vinnerbud', 'Vinnerbud + salær', 'Type', 'Objekt nr', 
        'Land', 'Utgave (for sedler), Pregested (for mynter)', 'Referanse', 
        'Referanse 2', 'Referanse 3', 'Referanse 4', 'Auksjonshus + auksjonsnummer', 
        'Info/kommentar/provinens', 'Proveniens', 'Konge'
    ]

    # Behold kun de kolonnene som faktisk finnes i dataframen
    columns = [col for col in columns if col in df.columns]

    df = df[columns]
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H-%M-%S")
    filename = f"auksjonsdata_{timestamp}.xlsx"
    latest_filename = "auksjonsdata_latest.xlsx"

    df.to_excel(filename, index=False)
    print(f"Data eksportert til {filename}")

    shutil.copy(filename, latest_filename)
    print(f"Siste versjon av filen lagret som {latest_filename}")

    return latest_filename






@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        print("Received POST request")  # ✅ Debug print 1
        max_auctions = request.form.get('max_auctions')
        max_items_per_auction = request.form.get('max_items_per_auction')
        max_auctions = int(max_auctions) if max_auctions and max_auctions.isdigit() else None
        max_items_per_auction = int(max_items_per_auction) if max_items_per_auction and max_items_per_auction.isdigit() else None
        
        try:
            print("Calling main()...")  # ✅ Debug print 2
            file_path = main(max_auctions, max_items_per_auction)
            print(f"File generated: {file_path}")  # ✅ Debug print 3
            print("Rendering success.html...")  # ✅ Debug print 4
            return render_template('success.html', file_path=file_path)
        except Exception as e:
            print(f"Error in main(): {e}")
            return str(e), 500  # Show the error in the browser

    return render_template('index.html')


@app.route('/download-latest')
def download_latest():
    filename = "auksjonsdata_latest.xlsx"
    if not os.path.exists(filename):
        return "No file found. Please run the scraper first.", 404
    return send_from_directory(directory='.', path=filename, as_attachment=True)

@app.route('/view-latest-data')
def view_latest_data():
    filename = "auksjonsdata_latest.xlsx"
    if not os.path.exists(filename):
        return "No data available. Please run the scraper first.", 404
    df = pd.read_excel(filename)
    return df.to_html()

if __name__ == "__main__":
    app.run(debug=True)