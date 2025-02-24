import requests
from bs4 import BeautifulSoup
import pandas as pd

def get_full_urls(url):
    """Henter alle auksjons-URLer fra en gitt side."""
    response = requests.get(url)
    html = response.text
    soup = BeautifulSoup(html, 'html.parser')
    events = soup.find_all('div', {'class': 'card-body event-row'})
    return [f"https://auksjon.oslomyntgalleri.no{event.find('a')['href']}" for event in events if event.find('a')]

def get_auction_item_urls(auction_url):
    """Henter alle objekt-URLer fra alle sider av en spesifikk auksjonsside."""
    item_urls = []
    current_page = 0
    while auction_url:
        response = requests.get(auction_url)
        html = response.text
        soup = BeautifulSoup(html, 'html.parser')
        gallery_units = soup.find_all('div', class_='galleryUnit')
        item_urls.extend(['https://auksjon.oslomyntgalleri.no' + unit.find('a')['href'] for unit in gallery_units if unit.find('a')])
        
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
            print(f"Moving to next page: {auction_url}")  # Logging for debugging
        else:
            print("No more pages found.")  # Logging for debugging
            auction_url = None

    return item_urls


def get_auction_item_data(item_url):
    """Henter detaljert informasjon om et auksjonsobjekt."""
    response = requests.get(item_url)
    html = response.text
    soup = BeautifulSoup(html, 'html.parser')
    
    item_data = {}
    item_data['Objekt'] = soup.find('h1', class_='detail__title').text.strip() if soup.find('h1', class_='detail__title') else ''
    item_data['Vinnerbud'] = soup.find('span', class_='NumberPart').text.strip().replace('\xa0', '') if soup.find('span', class_='NumberPart') else ''
    item_data['Objekt nr'] = soup.find('strong').text.replace('Objektnr.', '').strip() if soup.find('strong') else ''
    item_data['Land'] = soup.find('span', class_='detail__field-value').text.strip() if soup.find('span', class_='detail__field-value') else ''
    auction_info = soup.find('span', class_='h5').text.strip() if soup.find('span', class_='h5') else ''
    item_data['Auksjonshus + auksjonsnummer'] = auction_info
    return item_data


def main(max_auctions=None):
    base_url = 'https://auksjon.oslomyntgalleri.no/Events'
    page = 1
    all_auction_urls = []
    item_data_list = []

    while True:
        url = f"{base_url}?page={page}"
        new_urls = get_full_urls(url)
        if not new_urls:  # Stopper hvis det ikke er flere URL-er å hente
            break
        all_auction_urls.extend(new_urls)
        if max_auctions is not None and len(all_auction_urls) >= max_auctions:
            all_auction_urls = all_auction_urls[:max_auctions]  # Begrenser listen til det maksimale antallet auksjoner
            break
        page += 1  # Gå til neste side

    for auction_url in all_auction_urls:
        print(f"Besøker auksjonsside: {auction_url}")
        item_urls = get_auction_item_urls(auction_url)
        for item_url in item_urls:
            item_data = get_auction_item_data(item_url)
            item_data_list.append(item_data)

    # Eksportere dataene til en Excel-fil
    df = pd.DataFrame(item_data_list)
    df.to_excel('auksjonsdata.xlsx', index=False)
    print("Data eksportert til auksjonsdata.xlsx")

if __name__ == "__main__":
    main(max_auctions=1)