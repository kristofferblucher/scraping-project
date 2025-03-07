import shutil
from threading import Event
from flask import Flask, request, send_from_directory, render_template_string, render_template, jsonify, flash, redirect, url_for, session
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup
import pandas as pd
import datetime
import re
import os
import numpy as np
from scrapers.original_scraper import OriginalScraper
from scrapers.new_scraper import NewScraper



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
                print(f"\nSjekker auksjon tekst: {event_text}")
                
                # Spesialhåndtering for "nr. X" søk
                if 'nr.' in auction_name or 'nr ' in auction_name:
                    print(f"\nDEBUG: Full event text: {event_text}")
                    print(f"DEBUG: Søker i tekst: {auction_name}")
                    
                    # Finn søkenummeret først
                    search_number = re.search(r'nr\.?\s*(\d+)', auction_name)
                    if search_number:
                        search_num = search_number.group(1)
                        print(f"DEBUG: Søker etter auksjonsnummer: {search_num}")
                        
                        # Del opp teksten i deler basert på "nr."
                        parts = re.split(r'(nr\.?\s*\d+)', event_text)
                        
                        # Gå gjennom hver del og sjekk om den inneholder et nummer
                        for i, part in enumerate(parts):
                            if 'nr' in part.lower():
                                number_match = re.search(r'nr\.?\s*(\d+)', part.lower())
                                if number_match:
                                    found_num = number_match.group(1)
                                    print(f"DEBUG: Analyserer del: '{part}'")
                                    print(f"DEBUG: Fant nummer: {found_num}")
                                    
                                    # Sjekk om dette er et eksakt treff
                                    if found_num == search_num:
                                        # Sjekk konteksten rundt nummeret
                                        before = parts[i-1] if i > 0 else ""
                                        after = parts[i+1] if i < len(parts)-1 else ""
                                        
                                        # Sjekk at dette ikke er del av et større nummer
                                        if not re.search(r'\d+' + found_num, before) and \
                                           not re.search(found_num + r'\d+', after):
                                            print(f"DEBUG: ✓ Eksakt match funnet: {found_num}")
                                            matching_urls.append(f"{BASE_URL}{event.find('a')['href']}")
                                            print(f"Fant matching auksjon: {event_text}")
                                            break
                                    else:
                                        print(f"DEBUG: ✗ Ingen match: {found_num} != {search_num}")
                else:
                    # For andre søk, bruk den eksisterende logikken
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
            # Fjern eventuelle mellomrom i feltnavnet
            field_name = field_name.strip()
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

def get_scraper(source):
    return {
        'original': OriginalScraper(),
        'new': NewScraper()
    }.get(source, OriginalScraper())

def main(max_auctions=None, max_items_per_auction=None, search_term=None, search_term_year=None, custom_filename=None, auction_name=None, source='original'):
    global stop_event, scraping_active
    stop_event.clear()
    scraping_active = True
    
    try:
        print("Starting scraping process...")
        
        # Velg riktig scraper
        scraper = get_scraper(source)
        scraper.set_stop_event(stop_event)
        scraper.set_scraping_active(scraping_active)
        
        # Hent auksjoner
        all_auction_urls = scraper.get_auctions(max_auctions, auction_name)

        if not all_auction_urls:
            raise Exception("Ingen auksjoner matchet søket")

        if stop_event.is_set():
            raise Exception("Scraping was stopped")

        print(f"Total auctions found: {len(all_auction_urls)}")

        # Prosesser auksjoner
        item_data_list = []
        for auction_url in all_auction_urls:
            if stop_event.is_set():
                raise Exception("Scraping was stopped")
            
            items = scraper.process_auction(
                auction_url, 
                max_items=max_items_per_auction,
                search_term=search_term,
                search_term_year=search_term_year
            )
            item_data_list.extend(items)

        if not item_data_list:
            raise Exception("Ingen objekter matchet søkekriteriene")
        
        print("\nProcessing complete. Creating Excel file...")
        
        df = pd.DataFrame(item_data_list)

        # Sikre at alle ønskede kolonner eksisterer
        for col in ['Objekt', 'År', 'Kvalitet']:
            if col not in df.columns:
                df[col] = ''

        columns = [
            'Objekt', 'År', 'Kvalitet', 'Vinnerbud', 'Vinnerbud + salær', 'Type', 'Objekt nr', 
            'Land', 'Utgave (for sedler), Pregested (for mynter)', 'Referanse', 
            'Referanse 2', 'Referanse 3', 'Referanse 4', 'Auksjonshus + auksjonsnummer', 
            'Info/kommentar/provinens', 'Proveniens', 'Konge'
        ]
        # Filtrer kolonner som faktisk finnes i dataframen
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

        # Lagre til ny fil
        with pd.ExcelWriter(full_path, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        
        # Kopier til latest-filen og overskrive hvis den eksisterer
        if os.path.exists(latest_path):
            os.remove(latest_path)
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
    
    # Fjern alle flash-meldinger når siden lastes på nytt via GET
    if request.method == 'GET':
        session.clear()
    
    if request.method == 'POST':
        if scraping_active:
            flash("Scraping er allerede i gang", "error")
            return jsonify({
                "status": "error",
                "message": "Scraping er allerede i gang"
            }), 400

        try:
            # Hent form data
            source = request.form.get('source', 'original')
            max_auctions_input = request.form.get('max_auctions', '').strip()
            max_items_per_auction = request.form.get('max_items_per_auction', '').strip()
            search_term = request.form.get('search_term', '').strip()
            search_term_year = request.form.get('search_term_year', '').strip()
            custom_filename = request.form.get('custom_filename', '').strip()
            input_mode = request.form.get('input_mode', 'number')  # Hent input mode

            # Sjekk om max_auctions er et tall eller auksjonsnavn basert på modus
            auction_name = None
            max_auctions = None
            
            if max_auctions_input:
                if input_mode == 'number':
                    try:
                        max_auctions = int(max_auctions_input)
                        auction_name = None
                    except ValueError:
                        error_msg = f'"{max_auctions_input}" er ikke et gyldig tall for antall auksjoner'
                        flash(error_msg, "error")
                        return jsonify({
                            "status": "error",
                            "message": error_msg
                        }), 400
                else:
                    auction_name = max_auctions_input
                    max_auctions = None

            # Konverter max_items_per_auction til int hvis det er spesifisert
            if max_items_per_auction:
                try:
                    max_items_per_auction = int(max_items_per_auction)
                except ValueError:
                    error_msg = f'"{max_items_per_auction}" er ikke et gyldig tall for antall objekter'
                    flash(error_msg, "error")
                    return jsonify({
                        "status": "error",
                        "message": error_msg
                    }), 400

            print(f"Processed inputs: max_auctions={max_auctions}, auction_name={auction_name}, max_items={max_items_per_auction}")
            
            print("Calling main()...")
            try:
                result = main(
                    max_auctions=max_auctions,
                    max_items_per_auction=max_items_per_auction,
                    search_term=search_term,
                    search_term_year=search_term_year,
                    custom_filename=custom_filename,
                    auction_name=auction_name,
                    source=source
                )
            except Exception as e:
                error_message = str(e)
                if "Ingen auksjoner matchet søket" in error_message:
                    if auction_name:
                        error_message = f'Fant ingen auksjoner som matcher "{auction_name}"'
                    else:
                        error_message = "Fant ingen auksjoner"
                elif "Ingen objekter matchet søkekriteriene" in error_message:
                    error_message = "Fant ingen objekter som matcher søkekriteriene"
                
                flash(error_message, "error")
                return jsonify({
                    "status": "error",
                    "message": error_message
                }), 400
            
            if not result:
                error_msg = "Ingen resultater funnet"
                flash(error_msg, "error")
                return jsonify({
                    "status": "error",
                    "message": error_msg
                }), 400
            
            print(f"File generated: {result['path']}")
            
            # Hent auksjonshus navn basert på source
            auction_house = "Oslo Myntgalleri" if source == 'original' else "Meyer Eek"
            
            activity = (
                f"Scraping fullført\n"
                f"• Auksjonshus: {auction_house}\n"
                f"• Antall auksjoner: {max_auctions if max_auctions else 'alle'}\n"
                f"• Auksjonsnavn: {auction_name if auction_name else 'ikke spesifisert'}\n"
                f"• Antall objekter per auksjon: {max_items_per_auction if max_items_per_auction else 'alle'}\n"
                f"• Søkeord: {search_term if search_term else 'ingen'}\n"
                f"• Årstall: {search_term_year if search_term_year else 'ingen'}\n"
                f"• Egendefinert filnavn: {custom_filename if custom_filename else 'automatisk generert'}\n"
                f"• Generert fil: {result['download_filename']}"
            )
            log_user_activity(activity)
            
            flash("Scraping fullført!", "success")
            return jsonify({
                "status": "success",
                "file_path": result['path'],
                "download_filename": result['download_filename']
            })
                
        except Exception as e:
            print(f"Error in main(): {e}")
            scraping_active = False
            error_msg = str(e)
            flash(error_msg, "error")
            return jsonify({
                "status": "error",
                "message": error_msg
            }), 500

    return render_template('index.html', scraping_active=scraping_active)

@app.route('/stop', methods=['POST'])
def stop_scraping():
    global scraping_active, stop_event
    stop_event.set()  # Aktiverer stop-hendelsen
    scraping_active = False
    print("Scraping requested to stop.")
    
    # Logg at scrapingen ble stoppet manuelt
    activity = "Scraping stoppet manuelt av bruker"
    log_user_activity(activity)
    
    return jsonify({
        "status": "success",
        "message": "Scraping stopped"
    })

@app.route('/scraping-log')
def view_scraping_log():
    # Fjern alle flash-meldinger når man går til logg-siden
    session.clear()
    
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
