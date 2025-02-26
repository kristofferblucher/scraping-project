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
    item_data['Objekt'] = soup.find('h1', class_='detail__title').text.strip() if soup.find('h1', class_='detail__title') else ''
    item_data['Vinnerbud'] = soup.find('span', class_='NumberPart').text.strip().replace('\xa0', '') if soup.find('span', class_='NumberPart') else ''
    item_data['Objekt nr'] = soup.find('strong').text.replace('Objektnr.', '').strip() if soup.find('strong') else ''
    auction_info = soup.find('span', class_='h5').text.strip() if soup.find('span', class_='h5') else ''
    item_data['Auksjonshus + auksjonsnummer'] = auction_info

    # Henter alle "detail__custom-fields" data
    custom_fields = soup.find_all('div', class_='detail__custom-fields')
    for field in custom_fields:
        field_name = field.find('span', class_='detail__field-name').text.strip().replace(':', '') if field.find('span', class_='detail__field-name') else ''
        field_value = field.find('span', class_='detail__field-value').text.strip() if field.find('span', class_='detail__field-value') else ''
        if field_name and field_value:
            item_data[field_name] = field_value
            print(f"Field Name: {field_name}, Field Value: {field_value}")
            
            if field_name.lower() == 'land':
                item_data['Land'] = field_value

    # Beregn vinnerbud + salær (20 % tillegg)
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


def main():
    max_auctions = input("Hvor mange auksjoner vil du hente? (La tom for alle): ")
    max_items_per_auction = input("Hvor mange objekter per auksjon? (La tom for alle): ")

    max_auctions = int(max_auctions) if max_auctions.isdigit() else None
    max_items_per_auction = int(max_items_per_auction) if max_items_per_auction.isdigit() else None

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
            all_auction_urls = all_auction_urls[:max_auctions]
            break
        page += 1

    for auction_url in all_auction_urls:
        print(f"Besøker auksjonsside: {auction_url}")
        item_urls = get_auction_item_urls(auction_url, max_items_per_auction=max_items_per_auction)
        for item_url in item_urls:
            item_data = get_auction_item_data(item_url)
            item_data_list.append(item_data)

    df = pd.DataFrame(item_data_list)

    # Spesifiser ønsket kolonnerekkefølge med "Vinnerbud + salær" rett etter "Vinnerbud"
    columns = [
        'Objekt', 
        'Vinnerbud', 
        'Vinnerbud + salær',  # Legg denne rett etter "Vinnerbud"
        'Objekt nr', 
        'Land', 
        'Utgave (for sedler), Pregested (for mynter)', 
        'Referanse', 
        'Referanse 2', 
        'Referanse 3',
        'Referanse 4',
        'Auksjonshus + auksjonsnummer', 
        'Info/kommentar/provinens',
        'Proveniens',
        'proveniens',

    ]

    # Behold kun de kolonnene som faktisk finnes i dataframen
    columns = [col for col in columns if col in df.columns]

    df = df[columns]
    df.to_excel('auksjonsdata.xlsx', index=False)
    print("Data eksportert til auksjonsdata.xlsx")


if __name__ == "__main__":
    main()
