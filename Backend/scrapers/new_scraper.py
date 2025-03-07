from .base_scraper import BaseScraper
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import datetime

class NewScraper(BaseScraper):
    def __init__(self):
        super().__init__()
        self.BASE_URL = "https://www.meyereek.no"
        self.MAX_WORKERS = 10
        self.PREMIUM_PERCENTAGE = 1.2  # 20% tillegg for salær
        self.AUCTION_URLS = {
            'hovedauksjoner': f"{self.BASE_URL}/auksjoner/hovedauksjoner/",
            'nettauksjoner': f"{self.BASE_URL}/auksjoner/auksjoner-for-kunder-og-partnere/"
        }

    def set_stop_event(self, stop_event):
        """Setter stop_event for scraperen"""
        self.stop_event = stop_event

    def set_scraping_active(self, active):
        """Setter scraping_active status"""
        self.scraping_active = active

    def get_auctions(self, max_auctions=None, auction_name=None):
        """Henter alle auksjons-URLer fra Meyer Eek, både hovedauksjoner og nettauksjoner."""
        try:
            all_auction_urls = []
            
            # Først henter vi hovedauksjoner
            print("Henter hovedauksjoner...")
            hovedauksjoner = self._get_auctions_from_page(
                self.AUCTION_URLS['hovedauksjoner'], 
                auction_name
            )
            if hovedauksjoner:
                all_auction_urls.extend(hovedauksjoner)

            # Så henter vi nettauksjoner
            if not self.stop_event.is_set():
                print("Henter nettauksjoner...")
                nettauksjoner = self._get_auctions_from_page(
                    self.AUCTION_URLS['nettauksjoner'], 
                    auction_name
                )
                if nettauksjoner:
                    all_auction_urls.extend(nettauksjoner)

            # Begrens antall auksjoner hvis spesifisert
            if max_auctions and len(all_auction_urls) > max_auctions:
                all_auction_urls = all_auction_urls[:max_auctions]

            if not all_auction_urls:
                if auction_name:
                    raise Exception(f'Ingen auksjoner matchet søket "{auction_name}"')
                else:
                    raise Exception("Ingen auksjoner funnet")

            # Kun logg totalt antall hvis vi er i antall-modus
            if not auction_name or not auction_name.strip():
                print(f"Totalt antall auksjoner funnet: {len(all_auction_urls)}")
                
            return all_auction_urls

        except Exception as e:
            print(f"Feil ved henting av auksjoner: {str(e)}")
            raise

    def _get_auctions_from_page(self, url, auction_name=None):
        """Henter auksjons-URLer fra en spesifikk side."""
        if self.stop_event.is_set():
            return None

        try:
            print(f"Henter auksjoner fra: {url}")
            response = requests.get(url)
            soup = BeautifulSoup(response.text, 'html.parser')
            auction_urls = []

            # Finn alle auksjonskort
            auction_cards = soup.find_all('div', class_='card')
            
            for card in auction_cards:
                # Finn lenken i kortet
                link = card.find('a', class_='btn btn-primary btn-block stretched-link')
                if not link:
                    continue

                href = link['href']
                
                # Finn tittelen fra card-title span
                title_span = card.find('span', class_='card-title')
                auction_text = title_span.text.strip() if title_span else link.text.strip()
                print(f"Fant auksjon: {auction_text}")

                # Hvis det er spesifisert et auksjonsnavn
                if auction_name:
                    auction_name = auction_name.lower()
                    auction_text = auction_text.lower()
                    
                    # Hvis auction_name er bare et tall, konverter det til "nr. X" format
                    if auction_name.isdigit():
                        auction_name = f"nr. {auction_name}"
                    
                    # Spesialhåndtering for "nr. X" søk
                    if 'nr.' in auction_name or 'nr ' in auction_name:
                        # Finn søkenummeret
                        search_number = re.search(r'nr\.?\s*(\d+)', auction_name)
                        if search_number:
                            search_num = search_number.group(1)
                            
                            # Del opp auksjonsteksten i deler basert på "nr."
                            parts = re.split(r'(nr\.?\s*\d+)', auction_text)
                            
                            found_match = False
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
                                                auction_urls.append(href)
                                                print(f"Fant matching auksjon: {auction_text}")
                                                break
                    else:
                        # For andre søk, bruk den eksisterende logikken
                        search_terms = auction_name.split()
                        if all(term in auction_text for term in search_terms):
                            auction_urls.append(href)
                            print(f"Fant matching auksjon: {auction_text}")
                else:
                    auction_urls.append(href)

            print(f"Fant {len(auction_urls)} auksjoner på {url}")
            return auction_urls

        except Exception as e:
            print(f"Feil ved henting av auksjoner fra {url}: {str(e)}")
            return []

    def process_auction(self, url, max_items=None, search_term=None, search_term_year=None):
        """Prosesserer en enkelt auksjon og returnerer liste med item_data"""
        if self.stop_event.is_set():
            return []

        item_urls = self._get_auction_item_urls(url, max_items)
        if not item_urls:
            return []

        print(f"Starter prosessering av {len(item_urls)} objekter")
        item_data_list = []
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
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
                    # I antall-modus (ingen søkeord) legger vi til alle objekter
                    if not search_term and not search_term_year:
                        item_data_list.append(item_data)
                        print(f"La til objekt (antall-modus): {item_data.get('Objekt', 'Ukjent objekt')}")
                    # I søkemodus sjekker vi om objektet matcher kriteriene
                    elif self._matches_search_criteria(item_data, search_term, search_term_year):
                        item_data_list.append(item_data)
                        print(f"La til objekt (matchet søkekriterier): {item_data.get('Objekt', 'Ukjent objekt')}")
                    else:
                        print(f"Objekt matchet ikke søkekriteriene: {item_data.get('Objekt', 'Ukjent objekt')}")

        # Kast feilmelding kun hvis vi er i søkemodus og ikke fant noen treff
        if not item_data_list and (search_term or search_term_year):
            raise Exception("Ingen objekter matchet søkekriteriene")

        print(f"Fullførte prosessering av auksjon. Fant {len(item_data_list)} objekter")
        return item_data_list

    def _get_auction_item_urls(self, auction_url, max_items=None):
        """Henter alle objekt-URLer fra en auksjon."""
        item_urls = []
        page = 1
        
        while True:
            if self.stop_event.is_set():
                return None

            url = f"{auction_url}?page={page}"
            print(f"Henter objekter fra side {page}: {url}")  # Debug utskrift
            
            response = requests.get(url)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Finn alle objekter i galleriet
            # Prøv først med gallery-col-xxs
            gallery_items = soup.find_all('div', class_='gallery-col-xxs')
            
            # Hvis ikke funnet, prøv med lot-gallery-item
            if not gallery_items:
                gallery_items = soup.find_all('div', class_='lot-gallery-item')
            
            # Hvis fortsatt ikke funnet, prøv med generell gallery-item
            if not gallery_items:
                gallery_items = soup.find_all('div', class_=lambda x: x and ('gallery' in x.lower() or 'lot' in x.lower()))

            print(f"Fant {len(gallery_items)} objekter på side {page}")  # Debug utskrift
            
            if not gallery_items:
                break

            for item in gallery_items:
                if self.stop_event.is_set():
                    return None
                    
                # Finn lenken til objektet - prøv flere mulige mønstre
                item_link = None
                
                # Prøv først direkte lenke til LotDetails
                item_link = item.find('a', href=lambda x: x and '/Event/LotDetails/' in x)
                
                # Hvis ikke funnet, prøv andre mulige lenkeformater
                if not item_link:
                    item_link = item.find('a', href=lambda x: x and ('/Lot/' in x or '/lot/' in x))
                
                # Hvis fortsatt ikke funnet, prøv å finne første lenke med href
                if not item_link:
                    item_link = item.find('a', href=True)

                if item_link and item_link.get('href'):
                    item_url = item_link.get('href')
                    if not item_url.startswith('http'):
                        if not item_url.startswith('/'):
                            item_url = '/' + item_url
                        item_url = f"https://auksjon.meyereek.no{item_url}"
                    
                    # Unngå duplikater
                    if item_url not in item_urls:
                        item_urls.append(item_url)
                        print(f"Fant objekt: {item_url}")

            if max_items and len(item_urls) >= max_items:
                print(f"Nådde maksimalt antall objekter ({max_items})")
                item_urls = item_urls[:max_items]
                break

            # Sjekk etter neste side - prøv flere mulige klasser
            next_page = soup.find('a', class_='pagination-next')
            if not next_page:
                next_page = soup.find('a', class_=lambda x: x and ('next' in x.lower() or 'pagination-next' in x.lower()))
            
            if not next_page:
                print("Ingen flere sider funnet")
                break
                
            page += 1

        print(f"Totalt antall unike objekter funnet: {len(item_urls)}")  # Debug utskrift
        return item_urls

    def extract_item_data(self, item_url):
        """Henter detaljert informasjon om et auksjonsobjekt."""
        if self.stop_event.is_set():
            return None

        try:
            print(f"Prøver å hente data fra: {item_url}")
            response = requests.get(item_url)
            print(f"Status kode: {response.status_code}")
            soup = BeautifulSoup(response.text, 'html.parser')
            
            item_data = {}

            # Hent type fra keywords meta-tag
            keywords_meta = soup.find('meta', {'name': 'keywords'})
            if keywords_meta and keywords_meta.get('content'):
                keywords = keywords_meta['content'].split(',')
                if len(keywords) > 1:
                    item_data['Type'] = keywords[1].strip()
                else:
                    item_data['Type'] = keywords[0].strip()
            
            # Finn tittel-elementet
            title_element = soup.find('h1', class_='detail__title')
            if title_element:
                # Fjern eventuelle skjulte elementer
                for hidden in title_element.find_all('img'):
                    hidden.decompose()
                
                full_title = title_element.text.strip()
                print(f"Fant tittel: {full_title}")
                
                # Finn kvalitet - nå inkludert PMG.XX format
                quality_match = re.search(r'(?:Kv\.?\s*(\d+(?:/\d+)?(?:\+|-)?)|(?:PMG\.?\s*(\d+(?:\.\d+)?))|\b(\d+\s*(?:PMG|EPQ)(?:\s*[.,]\s*\d+)?)\b)', full_title)
                if quality_match:
                    # Finn den gruppen som matchet
                    quality = next(g for g in quality_match.groups() if g is not None)
                    item_data['Kvalitet'] = quality.strip()
                    full_title = full_title.replace(quality_match.group(0), '').strip()
                
                # Først, identifiser pengebeløp som er del av objektnavnet
                amount_words = ['kroner', 'øre', 'skilling', 'rigsbankdaler', 'speciedaler']
                amount_pattern = '|'.join(amount_words)
                amount_match = re.search(rf'(\d+)\s*(?:{amount_pattern})', full_title)
                
                if amount_match:
                    # Finn slutten av pengebeløpet
                    amount_end = amount_match.end()
                    # Finn årstall etter pengebeløpet
                    year_match = re.search(r'\b(\d{4}(?:\s*[A-Z]\.?\d+)?(?:\s*Specimen)?)\b', full_title[amount_end:])
                    
                    if year_match:
                        item_data['År'] = year_match.group(1).strip()
                        # Objektnavnet er alt før årstallet, men etter pengebeløpet
                        object_name = full_title[:amount_end].strip()
                        object_name = object_name.rstrip('.')
                        item_data['Objekt'] = object_name
                    else:
                        # Hvis vi ikke finner årstall, bruk hele tittelen som objektnavn
                        item_data['Objekt'] = full_title
                        item_data['År'] = ''
                else:
                    # Hvis vi ikke finner pengebeløp, prøv å finne årstall
                    year_match = re.search(r'\b(\d{4}(?:\s*[A-Z]\.?\d+)?(?:\s*Specimen)?)\b', full_title)
                    if year_match:
                        item_data['År'] = year_match.group(1).strip()
                        object_name = full_title[:year_match.start()].strip()
                        object_name = object_name.rstrip('.')
                        item_data['Objekt'] = object_name
                    else:
                        item_data['Objekt'] = full_title
                        item_data['År'] = ''
            else:
                print("Fant ikke tittel-element i det hele tatt")

            # Hent objektnummer fra text-center div
            obj_nr_element = soup.find('div', class_='text-center mb-2')
            if obj_nr_element:
                strong_element = obj_nr_element.find('strong')
                if strong_element:
                    obj_nr_text = strong_element.text.strip()
                    obj_nr_match = re.search(r'Objektnr\.\s*(\d+)', obj_nr_text)
                    if obj_nr_match:
                        item_data['Objekt nr'] = obj_nr_match.group(1)
                    else:
                        # Prøv å finne bare tallet
                        number_match = re.search(r'(\d+)', obj_nr_text)
                        if number_match:
                            item_data['Objekt nr'] = number_match.group(1)

            # Hvis vi fortsatt ikke har funnet objektnummer, prøv å finne det fra URL-en
            if 'Objekt nr' not in item_data:
                url_match = re.search(r'/(\d+)/', item_url)
                if url_match:
                    item_data['Objekt nr'] = url_match.group(1)

            # Hent vinnerbud fra list-group-item
            bid_element = soup.find('span', class_='NumberPart')
            print(f"Fant vinnerbud-element: {bid_element is not None}")
            if bid_element:
                bid_amount = bid_element.text.strip()
                bid_amount = re.sub(r'[^\d,.]', '', bid_amount)
                print(f"Fant vinnerbud: {bid_amount}")
                if bid_amount:
                    item_data['Vinnerbud'] = bid_amount
                    
                    try:
                        vinnerbud = float(bid_amount.replace('.', '').replace(',', '.'))
                        item_data['Vinnerbud + salær'] = f"{vinnerbud * self.PREMIUM_PERCENTAGE:,.2f}".replace(',', ' ').replace('.', ',')
                    except ValueError:
                        item_data['Vinnerbud + salær'] = ''

            # Hent auksjonsinformasjon
            auction_title = soup.find('span', class_='h5')
            if auction_title:
                item_data['Auksjonshus + auksjonsnummer'] = auction_title.text.strip()
            else:
                # Fallback til den gamle metoden hvis vi ikke finner h5
                auction_info = soup.find('div', {'data-listingid': True})
                if auction_info:
                    listing_id = auction_info.get('data-listingid')
                    item_data['Auksjonshus + auksjonsnummer'] = f"Meyer Eek AS - Auksjon {listing_id}"

            print(f"Returnerer data: {item_data}")
            return item_data

        except Exception as e:
            print(f"Feil ved henting av objektdata fra {item_url}: {str(e)}")
            return None

    def _matches_search_criteria(self, item_data, search_term=None, search_term_year=None):
        """Sjekker om et objekt matcher søkekriteriene."""
        # Hvis vi ikke har noen søkekriterier, returner True
        if (not search_term or not search_term.strip()) and (not search_term_year or not search_term_year.strip()):
            return True
            
        matches = True
        
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
                
        if search_term_year and search_term_year.strip() and matches:
            search_term_year = search_term_year.lower()
            year = item_data.get('År', '').lower()
            if search_term_year not in year:
                matches = False
                
        return matches 