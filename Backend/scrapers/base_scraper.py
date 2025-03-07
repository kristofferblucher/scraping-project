from abc import ABC, abstractmethod
from threading import Event

class BaseScraper(ABC):
    def __init__(self):
        self.progress = {'total': 0, 'processed': 0, 'current_status': ''}
        self.scraping_active = False
        self.stop_event = Event()

    @abstractmethod
    def get_auctions(self, max_auctions=None, auction_name=None):
        """Henter alle auksjons-URLer fra en gitt side."""
        pass

    @abstractmethod
    def process_auction(self, url, max_items=None, search_term=None, search_term_year=None):
        """Prosesserer en enkelt auksjon og returnerer liste med item_data"""
        pass

    @abstractmethod
    def extract_item_data(self, item_url):
        """Henter detaljert informasjon om et auksjonsobjekt."""
        pass

    def update_progress(self, total=None, processed=None, status=None):
        """Oppdaterer fremgangsinformasjon"""
        if total is not None:
            self.progress['total'] = total
        if processed is not None:
            self.progress['processed'] = processed
        if status is not None:
            self.progress['current_status'] = status

    def get_progress(self):
        """Henter nåværende fremgang"""
        return self.progress

    def stop(self):
        """Stopper scrapingen"""
        self.stop_event.set()
        self.scraping_active = False

    def start(self):
        """Starter scrapingen"""
        self.stop_event.clear()
        self.scraping_active = True
        self.progress = {'total': 0, 'processed': 0, 'current_status': 'Starter...'} 