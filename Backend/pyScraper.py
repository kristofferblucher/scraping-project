import shutil
from threading import Event
from flask import Flask, request, send_from_directory, render_template_string, render_template, jsonify, flash, redirect, url_for
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup
import pandas as pd
import datetime
import re
import os
import numpy as np



app = Flask(__name__)
app.secret_key = '1886Arsenal'  # Dette er en tilfeldig streng som brukes for å sikre session data
stop_event = Event()
scraping_active = False

# Finn mappen der denne .py-filen ligger
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Sett en variabel for mappen der Excel-filene skal ligge
OUTPUT_DIR = os.path.join(BASE_DIR, "excel_exports")

# Opprett mappen om den ikke finnes
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Constants
BASE_URL = 'https://auksjon.oslomyntgalleri.no'
DEFAULT_MAX_WORKERS = 10
PREMIUM_PERCENTAGE = 1.2  # 20% addition for premium

def get_full_urls(url, auction_name=None):
    """Henter alle auksjons-URLer fra en gitt side."""
    response = requests.get(url)
    html = response.text
    soup = BeautifulSoup(html, 'html.parser')
    events = soup.find_all('div', {'class': 'card-body event-row'})
    
    if auction_name:
        auction_name = auction_name.lower()
        matching_urls = []
        for event in events:
            if event.find('a'):
                # Hent all tekst fra event-raden
                event_text = event.get_text(separator=' ', strip=True).lower()
                # Sjekk om ALLE søkeordene finnes i teksten
                search_terms = auction_name.split()
                if all(term in event_text for term in search_terms):
                    matching_urls.append(f"{BASE_URL}{event.find('a')['href']}")
                    print(f"Fant matching auksjon: {event_text}")
        return matching_urls
    return [f"{BASE_URL}{event.find('a')['href']}" for event in events if event.find('a')]


def get_auction_item_urls(auction_url, max_items_per_auction=None):
    """Henter alle objekt-URLer fra alle sider av en spesifikk auksjonsside."""
    item_urls = []
    current_page = 0
    
    # Konverter max_items_per_auction til int hvis det er en streng
    if isinstance(max_items_per_auction, str):
        try:
            max_items_per_auction = int(max_items_per_auction)
        except ValueError:
            print(f"Ugyldig verdi for max_items_per_auction: {max_items_per_auction}")
            max_items_per_auction = None
    
    while auction_url:
        if stop_event.is_set():
            return None

        response = requests.get(auction_url)
        html = response.text
        soup = BeautifulSoup(html, 'html.parser')
        gallery_units = soup.find_all('div', class_='galleryUnit')
        item_urls.extend([f"{BASE_URL}{unit.find('a')['href']}" for unit in gallery_units if unit.find('a')])
        
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
            auction_url = f"{BASE_URL}{next_page_link['href']}"
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
    
    # Forbedret regex som håndterer tekst før kroner-beløpet
    kroner_match = re.search(r'(.*?)(\d+\s*kroner)(.*)', full_title, re.IGNORECASE)
    if kroner_match:
        # Gruppe 1: tekst før kroner
        # Gruppe 2: kroner-beløpet
        # Gruppe 3: tekst etter kroner
        prefix = kroner_match.group(1).strip()
        kroner = kroner_match.group(2).strip()
        rest = kroner_match.group(3).strip()
        
        # Kombiner prefix og kroner for objektnavnet
        item_data['Objekt'] = f"{prefix} {kroner}".strip()
        
        # Se etter årstall i resten av teksten
        year_match = re.search(r'\b(\d{4})\b', rest)
        if year_match:
            year_index = year_match.start(0)
            item_data['År'] = rest[year_index:].strip()
            # Legg til eventuell tekst mellom kroner og årstall i objektnavnet
            if year_index > 0:
                item_data['Objekt'] += " " + rest[:year_index].strip()
        else:
            item_data['År'] = ''
    else:
        # Hvis ingen kroner-mønster, se etter årstall
        year_match = re.search(r'\b(\d{4})\b', full_title)
        if year_match:
            year_index = year_match.start(0)
            item_data['Objekt'] = full_title[:year_index].strip()
            item_data['År'] = full_title[year_index:].strip()
        else:
            item_data['Objekt'] = full_title
            item_data['År'] = ''

    # Logg splitting for debugging
    print(f"Original tittel: '{full_title}'")
    print(f"Split til: Objekt='{item_data['Objekt']}', År='{item_data['År']}'")

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
            item_data['Vinnerbud + salær'] = f"{vinnerbud * PREMIUM_PERCENTAGE:,.2f}".replace(',', ' ').replace('.', ',')
        except ValueError:
            item_data['Vinnerbud + salær'] = ''
    else:
        item_data['Vinnerbud + salær'] = ''

    return item_data


def matches_search_criteria(item_data, search_term=None, search_term_year=None):
    """
    Sjekker om et objekt matcher søkekriteriene.
    
    Args:
        item_data (dict): Data for auksjonsobjektet
        search_term (str, optional): Søketekst som skal matches mot objektnavnet
        search_term_year (str, optional): Årstall som skal matches
        
    Returns:
        bool: True hvis objektet matcher kriteriene, False ellers
    """
    if not search_term and not search_term_year:
        return True
        
    matches = True
    
    if search_term:
        search_term = search_term.lower()
        object_name = item_data.get('Objekt', '').lower()
        if search_term not in object_name:
            matches = False
            
    if search_term_year and matches:
        search_term_year = search_term_year.lower()
        year = item_data.get('År', '').lower()
        if search_term_year not in year:
            matches = False
            
    return matches

def main(max_auctions=None, max_items_per_auction=None, search_term=None, search_term_year=None, custom_filename=None, auction_name=None):
    global stop_event, scraping_active
    stop_event.clear()
    scraping_active = True
    
    try:
        print("Starting scraping process...")
        
        # Konverter max_auctions til int hvis det er en streng
        if isinstance(max_auctions, str):
            try:
                max_auctions = int(max_auctions)
            except ValueError:
                print(f"Ugyldig verdi for max_auctions: {max_auctions}")
                max_auctions = None
        
        if stop_event.is_set():
            raise Exception("Scraping was stopped")

        base_url = f'{BASE_URL}/Events'
        page = 1
        all_auction_urls = []
        
        # Samle inn auksjons-URLer
        while True and not stop_event.is_set():
            url = f"{base_url}?page={page}"
            print(f"Fetching auctions from page {page}")
            new_urls = get_full_urls(url, auction_name)
            
            if not new_urls:
                break
                
            all_auction_urls.extend(new_urls)
            print(f"Found {len(new_urls)} auctions on page {page}")
            
            if not auction_name and max_auctions is not None and len(all_auction_urls) >= max_auctions:
                all_auction_urls = all_auction_urls[:max_auctions]
                break
                
            page += 1

        if not all_auction_urls:
            raise Exception("Ingen auksjoner matchet søket")

        if stop_event.is_set():
            raise Exception("Scraping was stopped")

        print(f"Total auctions found: {len(all_auction_urls)}")

        # Process items
        item_data_list = []
        with ThreadPoolExecutor(max_workers=DEFAULT_MAX_WORKERS) as executor:
            futures = []
            for auction_url in all_auction_urls:
                if stop_event.is_set():
                    raise Exception("Scraping was stopped")
                    
                item_urls = get_auction_item_urls(auction_url, max_items_per_auction)
                if item_urls:
                    for item_url in item_urls:
                        futures.append(executor.submit(get_auction_item_data, item_url))

            for future in as_completed(futures):
                if stop_event.is_set():
                    raise Exception("Scraping was stopped")

                item_data = future.result()
                if item_data:
                    if matches_search_criteria(item_data, search_term, search_term_year):
                        item_data_list.append(item_data)

        if not item_data_list:
            raise Exception("Ingen objekter matchet søkekriteriene")
        
        print("\nProcessing complete. Creating Excel file...")
        
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

        # Handle filename
        if custom_filename:
            custom_filename = custom_filename.replace('.xlsx', '')
            custom_filename = re.sub(r'[<>:"/\\|?*]', '_', custom_filename)
            filename = f"{custom_filename}.xlsx"
            counter = 1
            while os.path.exists(os.path.join(OUTPUT_DIR, filename)):
                filename = f"{custom_filename}_{counter}.xlsx"
                counter += 1
        else:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H-%M-%S")
            filename = f"auksjonsdata_{timestamp}.xlsx"

        latest_filename = "auksjonsdata_latest.xlsx"
        
        full_path = os.path.join(OUTPUT_DIR, filename)
        latest_path = os.path.join(OUTPUT_DIR, latest_filename)

        df.to_excel(full_path, index=False)
        shutil.copy(full_path, latest_path)

        print(f"📁 Data eksportert til {filename}")
        return {'path': latest_path, 'download_filename': filename}
        
    except Exception as e:
        print(f"Error in main: {str(e)}")
        raise e
    
    finally:
        scraping_active = False

def log_user_activity(activity):
    with open('scraping_log.txt', 'a', encoding='utf-8') as file:
        timestamp = datetime.datetime.now().isoformat()
        file.write(f"{timestamp}: {activity}\n")

@app.route('/', methods=['GET', 'POST'])
def index():
    global scraping_active

    if request.method == 'POST':
        if scraping_active:
            flash('Scraping pågår allerede. Vennligst vent eller stopp den nåværende prosessen.', 'error')
            return render_template('index.html', scraping_active=scraping_active)
        
        print("Received POST request")
        
        try:
            # Hent form data
            max_auctions_input = request.form.get('max_auctions', '').strip()
            input_mode = request.form.get('input_mode', 'number').strip()
            max_items_per_auction = request.form.get('max_items_per_auction', '').strip()
            search_term = request.form.get('search_term', '').strip()
            search_term_year = request.form.get('search_term_year', '').strip()
            custom_filename = request.form.get('custom_filename', '').strip()

            # Sjekk om max_auctions er et tall eller auksjonsnavn basert på input_mode
            auction_name = None
            max_auctions = None
            
            if max_auctions_input:
                if input_mode == 'number':
                    try:
                        max_auctions = int(max_auctions_input)
                        if max_auctions <= 0:
                            flash('Antall auksjoner må være et positivt tall.', 'error')
                            return render_template('index.html', scraping_active=scraping_active)
                        auction_name = None
                    except ValueError:
                        flash('Vennligst skriv inn et gyldig tall for antall auksjoner.', 'error')
                        return render_template('index.html', scraping_active=scraping_active)
                else:  # input_mode == 'name'
                    auction_name = max_auctions_input
                    max_auctions = None

            # Konverter max_items_per_auction til int hvis det er spesifisert
            if max_items_per_auction:
                try:
                    max_items_per_auction = int(max_items_per_auction)
                    if max_items_per_auction <= 0:
                        flash('Antall objekter per auksjon må være et positivt tall.', 'error')
                        return render_template('index.html', scraping_active=scraping_active)
                except ValueError:
                    flash('Antall objekter per auksjon må være et tall.', 'error')
                    return render_template('index.html', scraping_active=scraping_active)

            print(f"Processed inputs: max_auctions={max_auctions}, auction_name={auction_name}, max_items={max_items_per_auction}")
            
            print("Calling main()...")
            result = main(
                max_auctions=max_auctions,
                max_items_per_auction=max_items_per_auction,
                search_term=search_term,
                search_term_year=search_term_year,
                custom_filename=custom_filename,
                auction_name=auction_name
            )
            
            if not result:
                scraping_active = False
                flash('Ingen resultater funnet.', 'error')
                return render_template('index.html', scraping_active=scraping_active)
            
            print(f"File generated: {result['path']}")
            
            activity = (
                f"Scraping fullført\n"
                f"• Antall auksjoner: {max_auctions if max_auctions else 'alle'}\n"
                f"• Auksjonsnavn: {auction_name if auction_name else 'ikke spesifisert'}\n"
                f"• Antall objekter per auksjon: {max_items_per_auction if max_items_per_auction else 'alle'}\n"
                f"• Søkeord: {search_term if search_term else 'ingen'}\n"
                f"• Årstall: {search_term_year if search_term_year else 'ingen'}\n"
                f"• Egendefinert filnavn: {custom_filename if custom_filename else 'automatisk generert'}\n"
                f"• Generert fil: {result['download_filename']}"
            )
            log_user_activity(activity)
            
            return render_template('success.html', 
                                file_path=result['path'], 
                                download_filename=result['download_filename'],
                                scraping_active=False)
                
        except Exception as e:
            print(f"Error in main(): {str(e)}")
            scraping_active = False
            flash(str(e), 'error')
            return render_template('index.html', scraping_active=scraping_active)

    return render_template('index.html', scraping_active=scraping_active)

@app.route('/stop', methods=['POST'])
def stop_scraping():
    global scraping_active
    stop_event.set()  # Aktiverer stop-hendelsen
    print("Scraping requested to stop.")
    scraping_active = False
    
    # Logg at scrapingen ble stoppet manuelt
    activity = "Scraping stoppet manuelt av bruker"
    log_user_activity(activity)
    
    return render_template('index.html', scraping_active=scraping_active)

@app.route('/scraping-log')
def view_scraping_log():
    log_file = 'scraping_log.txt'
    
    # Sjekk om filen eksisterer før du prøver å lese den
    if not os.path.exists(log_file):
        log_entries = []  # Hvis filen ikke finnes, bruk en tom liste
    else:
        try:
            with open(log_file, 'r', encoding='utf-8') as file:
                content = file.read()
                # Del innholdet på ISO timestamp-mønsteret (YYYY-MM-DD...)
                import re
                log_entries = []
                # Finn alle timestamps i filen
                timestamps = re.finditer(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+', content)
                # Konverter iterator til liste av start-posisjoner
                positions = [m.start() for m in timestamps]
                
                # Behandle hver loggoppføring
                for i in range(len(positions)):
                    start = positions[i]
                    # Hvis dette er siste oppføring, bruk resten av filen
                    end = positions[i + 1] if i < len(positions) - 1 else len(content)
                    entry = content[start:end].strip()
                    if entry:  # Legg bare til hvis oppføringen ikke er tom
                        log_entries.append(entry)
        except UnicodeDecodeError:
            # Hvis filen allerede inneholder data med feil encoding, prøv å lese den med 'latin-1'
            with open(log_file, 'r', encoding='latin-1') as file:
                content = file.read()
                # Konverter innholdet til UTF-8 og skriv det tilbake
                with open(log_file, 'w', encoding='utf-8') as outfile:
                    outfile.write(content)
                # Del opp innholdet som før
                log_entries = []
                timestamps = re.finditer(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+', content)
                positions = [m.start() for m in timestamps]
                for i in range(len(positions)):
                    start = positions[i]
                    end = positions[i + 1] if i < len(positions) - 1 else len(content)
                    entry = content[start:end].strip()
                    if entry:
                        log_entries.append(entry)

    return render_template('scraping_log.html', log_entries=log_entries)

@app.route('/download-latest')
def download_latest():
    filename = 'auksjonsdata_latest.xlsx'
    full_path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(full_path):
        return "No file found. Please run the scraper first.", 404

    # Hent filnavnet fra success-siden hvis det finnes
    download_filename = request.args.get('filename', filename)
    
    return send_from_directory(
        directory=OUTPUT_DIR, 
        path=filename, 
        as_attachment=True,
        download_name=download_filename
    )

@app.route('/view-latest-data')
def view_latest_data():
    filename = "auksjonsdata_latest.xlsx"
    full_path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(full_path):
        return "No data available. Please run the scraper first.", 404
    df = pd.read_excel(full_path)
    return df.to_html()

if __name__ == "__main__":
    app.run(debug=True)
