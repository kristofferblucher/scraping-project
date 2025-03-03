# app.py (Flask Application)
from flask import Flask, request, jsonify
import pyScraper  # This would be your existing Python script

app = Flask(__name__)

@app.route('/scrape', methods=['POST'])
def scrape():
    data = request.json
    max_auctions = data.get('maxAuctions')
    max_items_per_auction = data.get('maxItemsPerAuction')
    # Run your scraping function
    results = my_scraper_script.run_scraping(max_auctions, max_items_per_auction)
    return jsonify(results)

if __name__ == '__main__':
    app.run(debug=True)
