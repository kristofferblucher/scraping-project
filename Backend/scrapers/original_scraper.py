from .base_scraper import BaseScraper
import requests
from bs4 import BeautifulSoup
import pandas as pd
import datetime
import re
import os
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed

class OriginalScraper(BaseScraper):
    def __init__(self):
        super().__init__()
        self.BASE_URL = 'https://auksjon.oslomyntgalleri.no'
        self.DEFAULT_MAX_WORKERS = 10
        self.PREMIUM_PERCENTAGE = 1.2  # 20% addition for premium
        self.stop_event = None
        self.scraping_active = None

    def set_stop_event(self, stop_event):
        self.stop_event = stop_event

    def set_scraping_active(self, scraping_active):
        self.scraping_active = scraping_active

    def get_auctions(self, max_auctions=None, auction_name=None):
        """Henter alle auksjons-URLer fra en gitt side."""
        base_url = f'{self.BASE_URL}/Events'
        page = 0
        all_auction_urls = []
        
        # Konverter max_auctions til int hvis det er en streng
        if isinstance(max_auctions, str):
            try:
                max_auctions = int(max_auctions)
            except ValueError:
                print(f"Ugyldig verdi for max_auctions: {max_auctions}")
                max_auctions = None
        
        if self.stop_event.is_set():
            raise Exception("Scraping was stopped")

        while True and not self.stop_event.is_set():
            url = f"{base_url}?page={page}"
            print(f"Henter auksjoner fra side {page}")
            response = requests.get(url)
            html = response.text
            soup = BeautifulSoup(html, 'html.parser')
            events = soup.find_all('div', {'class': 'card-body event-row'})
            
            # Hvis ingen flere events, avslutt loopen
            if not events:
                break
                
            # Hvis vi har et auksjonsnavn og det ikke er tomt, bruk søkemodus
            if auction_name and auction_name.strip():
                auction_name = auction_name.lower()
                matching_urls = []
                for event in events:
                    if event.find('a'):
                        event_text = event.get_text(separator=' ', strip=True).lower()
                        
                        # Hvis auction_name er bare et tall, konverter det til "nr. X" format
                        if auction_name.isdigit():
                            auction_name = f"nr. {auction_name}"
                        
                        # Spesialhåndtering for "nr. X" søk
                        if 'nr.' in auction_name or 'nr ' in auction_name:
                            # Finn søkenummeret
                            search_number = re.search(r'nr\.?\s*(\d+)', auction_name)
                            if search_number:
                                search_num = search_number.group(1)
                                
                                # Del opp teksten i deler basert på "nr."
                                parts = re.split(r'(nr\.?\s*\d+)', event_text)
                                
                                # Gå gjennom hver del og sjekk om den inneholder et nummer
                                for i, part in enumerate(parts):
                                    if 'nr' in part.lower():
                                        number_match = re.search(r'nr\.?\s*(\d+)', part.lower())
                                        if number_match:
                                            found_num = number_match.group(1)
                                            
                                            # Sjekk om dette er et eksakt treff
                                            if found_num == search_num:
                                                # Sjekk konteksten rundt nummeret
                                                before = parts[i-1] if i > 0 else ""
                                                after = parts[i+1] if i < len(parts)-1 else ""
                                                
                                                # Sjekk at dette ikke er del av et større nummer
                                                if not re.search(r'\d+' + found_num, before) and \
                                                   not re.search(found_num + r'\d+', after):
                                                    matching_urls.append(f"{self.BASE_URL}{event.find('a')['href']}")
                                                    print(f"Fant matching auksjon: {event_text}")
                                                    break
                        else:
                            # For andre søk, bruk den eksisterende logikken
                            search_terms = auction_name.split()
                            if all(term in event_text for term in search_terms):
                                matching_urls.append(f"{self.BASE_URL}{event.find('a')['href']}")
                                print(f"Fant matching auksjon: {event_text}")
                all_auction_urls.extend(matching_urls)
            else:
                # Hvis vi ikke har auksjonsnavn eller det er tomt, bruk antall-modus
                new_urls = [f"{self.BASE_URL}{event.find('a')['href']}" for event in events if event.find('a')]
                print(f"Antall-modus: Fant {len(new_urls)} auksjoner på side {page}")
                all_auction_urls.extend(new_urls)
                
                # I antall-modus, stopp når vi har nok auksjoner
                if max_auctions is not None and len(all_auction_urls) >= max_auctions:
                    all_auction_urls = all_auction_urls[:max_auctions]
                    print(f"Nådde ønsket antall auksjoner: {max_auctions}")
                    break
            
            # Gå til neste side
            page += 1
            
            # Hvis ingen nye URLs ble funnet på denne siden, avslutt loopen
            if not events:
                break

        if not all_auction_urls:
            if auction_name and auction_name.strip():
                raise Exception(f'Ingen auksjoner matchet søket "{auction_name}"')
            else:
                raise Exception("Ingen auksjoner funnet")

        # Kun logg totalt antall hvis vi er i antall-modus
        if not auction_name or not auction_name.strip():
            print(f"Totalt antall auksjoner funnet: {len(all_auction_urls)}")
            
        return all_auction_urls

    def process_auction(self, url, max_items=None, search_term=None, search_term_year=None):
        """Prosesserer en enkelt auksjon og returnerer liste med item_data"""
        if self.stop_event.is_set():
            return []

        item_urls = self.get_auction_item_urls(url, max_items)
        if not item_urls:
            return []

        print(f"Starter prosessering av {len(item_urls)} objekter")
        item_data_list = []
        with ThreadPoolExecutor(max_workers=self.DEFAULT_MAX_WORKERS) as executor:
            futures = []
            for item_url in item_urls:
                if self.stop_event.is_set():
                    return item_data_list
                futures.append(executor.submit(self.extract_item_data, item_url))

            for future in as_completed(futures):
                if self.stop_event.is_set():
                    return item_data_list

                item_data = future.result()
                if item_data:
                    # Hvis vi ikke har søkekriterier (antall-modus) eller objektet matcher søkekriteriene
                    if (not search_term or not search_term.strip()) and (not search_term_year or not search_term_year.strip()):
                        item_data_list.append(item_data)
                        print(f"La til objekt: {item_data.get('Objekt', 'Ukjent objekt')}")
                    elif self.matches_search_criteria(item_data, search_term, search_term_year):
                        item_data_list.append(item_data)
                        print(f"La til objekt (matchet søkekriterier): {item_data.get('Objekt', 'Ukjent objekt')}")
                    else:
                        print(f"Objekt matchet ikke søkekriteriene: {item_data.get('Objekt', 'Ukjent objekt')}")

        print(f"Fullførte prosessering av auksjon. Fant {len(item_data_list)} objekter")
        return item_data_list

    def get_auction_item_urls(self, auction_url, max_items_per_auction=None):
        """Henter alle objekt-URLer fra en auksjonsside."""
        item_urls = []
        current_page = 0
        
        # Konverter max_items_per_auction til int hvis det er en streng
        if isinstance(max_items_per_auction, str):
            try:
                max_items_per_auction = int(max_items_per_auction)
            except ValueError:
                print(f"Ugyldig verdi for max_items_per_auction: {max_items_per_auction}")
                max_items_per_auction = None
        
        while auction_url and not self.stop_event.is_set():
            try:
                # Sjekk om URL-en er en PDF
                if '.pdf' in auction_url.lower():
                    print("PDF-fil oppdaget - hopper over denne auksjonen")
                    break
                    
                response = requests.get(auction_url)
                html = response.text
                soup = BeautifulSoup(html, 'html.parser')
                gallery_units = soup.find_all('div', class_='galleryUnit')
                
                # Sikre at alle URLer har full path
                for unit in gallery_units:
                    if unit.find('a'):
                        href = unit.find('a')['href']
                        if href.startswith('/'):
                            item_urls.append(f"{self.BASE_URL}{href}")
                        elif href.startswith('http'):
                            item_urls.append(href)
                        else:
                            item_urls.append(f"{self.BASE_URL}/{href}")
                
                if max_items_per_auction is not None and len(item_urls) >= max_items_per_auction:
                    item_urls = item_urls[:max_items_per_auction]
                    break
                
                pagination_links = soup.find_all('a', href=True)
                next_page_link = None
                for link in pagination_links:
                    if 'page=' + str(current_page + 1) in link['href']:
                        next_page_link = link
                        break

                if next_page_link:
                    next_url = next_page_link['href']
                    if next_url.startswith('/'):
                        auction_url = f"{self.BASE_URL}{next_url}"
                    elif next_url.startswith('http'):
                        auction_url = next_url
                    else:
                        auction_url = f"{self.BASE_URL}/{next_url}"
                    current_page += 1
                    print(f"Går til neste side: {auction_url}")
                else:
                    print("Ingen flere sider funnet.")
                    auction_url = None
                    
            except Exception as e:
                print(f"Feil ved henting av objekter: {str(e)}")
                break

        return item_urls

    def extract_item_data(self, item_url):
        """Henter detaljert informasjon om et auksjonsobjekt."""
        if self.stop_event.is_set():
            return None

        response = requests.get(item_url)
        html = response.text
        soup = BeautifulSoup(html, 'html.parser')
        
        item_data = {}
        full_title = soup.find('h1', class_='detail__title').text.strip() if soup.find('h1', class_='detail__title') else ''
        
        kroner_match = re.search(r'(.*?)(\d+\s*kroner)(.*)', full_title, re.IGNORECASE)
        if kroner_match:
            prefix = kroner_match.group(1).strip()
            kroner = kroner_match.group(2).strip()
            rest = kroner_match.group(3).strip()
            
            item_data['Objekt'] = f"{prefix} {kroner}".strip()
            
            year_match = re.search(r'\b(\d{4})\b', rest)
            if year_match:
                year_index = year_match.start(0)
                item_data['År'] = rest[year_index:].strip()
                if year_index > 0:
                    item_data['Objekt'] += " " + rest[:year_index].strip()
            else:
                item_data['År'] = ''
        else:
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

        subtitle = soup.find('span', class_='lead detail__subtitle')
        item_data['Konge'] = subtitle.text.strip() if subtitle else ''

        keywords_meta = soup.find('meta', {'name': 'keywords'})
        item_data['Type'] = keywords_meta['content'] if keywords_meta else ''

        item_data['Vinnerbud'] = soup.find('span', class_='NumberPart').text.strip().replace('\xa0', '') if soup.find('span', class_='NumberPart') else ''

        strong_tags = soup.find_all('strong')
        for strong_tag in strong_tags:
            if 'objekt' in strong_tag.text.lower():
                item_data['Objekt nr'] = strong_tag.text.replace('Objektnr.', '').strip()
                break
        
        auction_info = soup.find('span', class_='h5').text.strip() if soup.find('span', class_='h5') else ''
        item_data['Auksjonshus + auksjonsnummer'] = auction_info

        custom_fields = soup.find_all('div', class_='detail__custom-fields')
        for field in custom_fields:
            field_name = field.find('span', class_='detail__field-name').text.strip().replace(':', '') if field.find('span', class_='detail__field-name') else ''
            field_value = field.find('span', class_='detail__field-value').text.strip() if field.find('span', class_='detail__field-value') else ''
            if field_name and field_value:
                field_name = field_name.strip()
                item_data[field_name] = field_value

        if item_data['Vinnerbud']:
            try:
                vinnerbud = float(item_data['Vinnerbud'].replace('.', '').replace(',', '.'))
                item_data['Vinnerbud + salær'] = f"{vinnerbud * self.PREMIUM_PERCENTAGE:,.2f}".replace(',', ' ').replace('.', ',')
            except ValueError:
                item_data['Vinnerbud + salær'] = ''
        else:
            item_data['Vinnerbud + salær'] = ''

        return item_data

    def matches_search_criteria(self, item_data, search_term=None, search_term_year=None):
        """Sjekker om et objekt matcher søkekriteriene."""
        # Hvis vi ikke har noen søkekriterier, returner True
        if (not search_term or not search_term.strip()) and (not search_term_year or not search_term_year.strip()):
            return True
            
        # Hvis vi har søkekriterier, sjekk dem
        matches = True
        
        # Sjekk søkeord hvis det er spesifisert og ikke tomt
        if search_term and search_term.strip():
            search_term = search_term.lower()
            object_name = item_data.get('Objekt', '').lower()
            
            # Spesialhåndtering for beløp-søk
            if search_term.isdigit():
                # Finn alle beløp i objektnavnet
                amounts = re.findall(r'\b(\d+)\s*(?:kroner|øre|skilling|rigsbankdaler|speciedaler)\b', object_name)
                if amounts:
                    # Sjekk om søketermen matcher nøyaktig med noen av beløpene
                    matches = search_term in amounts
                else:
                    matches = False
            else:
                # For ikke-numeriske søk, bruk vanlig inneholdt-sjekk
                matches = search_term in object_name
                
        # Sjekk årstall hvis det er spesifisert og ikke tomt
        if search_term_year and search_term_year.strip() and matches:
            search_term_year = search_term_year.lower()
            year = item_data.get('År', '').lower()
            if search_term_year not in year:
                matches = False
                
        return matches 