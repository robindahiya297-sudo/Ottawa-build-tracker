import os
import re
import json
import requests
import gspread
from bs4 import BeautifulSoup

def get_ottawa_builds():
    # Targets public consumer aggregator listings for Ottawa New Construction
    url = "https://www.zillow.com/ottawa-on/new-homes/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9"
    }
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            print(f"Error fetching data: HTTP {response.status_code}")
            return []
            
        soup = BeautifulSoup(response.text, 'html.parser')
        listings = []
        
        # Look for the JSON script tag Zillow embeds with raw listing data
        script_tag = soup.find("script", id="___gcfg") or soup.find("script", string=re.compile("queryState"))
        
        # Alternative fallback: Parse structural cards directly from public markup
        cards = soup.find_all("div", class_=re.compile("StyledCard")) or soup.find_all("li", class_=re.compile("ListItem"))
        
        for card in cards:
            try:
                price_text = card.find(text=re.compile(r"C?\$")) or card.find(class_=re.compile("Price"))
                price = price_text.strip() if price_text else "Contact Builder"
                
                title_text = card.find(text=re.compile("Plan|Homes|Model")) or card.find(address=True)
                title = title_text.strip() if title_text else "New Construction Model"
                
                # Default parse logic for major builders text tags
                builder = "Unknown Builder"
                for b in ["Mattamy", "Minto", "Caivan", "Claridge", "Richcraft", "Urbandale"]:
                    if b.lower() in card.text.lower():
                        builder = f"{b} Homes"
                        break
                
                # Break down basic specs strings
                specs = "3 bds, 2.5 ba"
                for spec_match in re.findall(r"\d+\s?bds|\d+\s?ba|\d+,?\d+\s?sqft", card.text):
                    specs += f", {spec_match}"
                
                neighborhood = "Ottawa Region"
                for n in ["Kanata", "Barrhaven", "Orleans", "Stittsville", "Manotick", "Greely"]:
                    if n.lower() in card.text.lower():
                        neighborhood = n
                        break
                        
                link_tag = card.find("a", href=True)
                link = "https://www.zillow.com" + link_tag['href'] if link_tag and link_tag['href'].startswith("/") else (link_tag['href'] if link_tag else "https://www.zillow.com")
                
                listings.append([title, builder, price, neighborhood, specs, link])
            except Exception as e:
                continue
        return listings
    except Exception as e:
        print(f"Scraper encountered an error: {e}")
        return []

def sync_to_sheets():
    # Pull the credentials from your GitHub Repository Secrets
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        print("Missing GOOGLE_CREDENTIALS secret!")
        return
        
    creds_data = json.loads(creds_json)
    gc = gspread.service_account_from_dict(creds_data)
    
    # Connects to your Google Sheet template
    try:
        sheet = gc.open("Ottawa New Builds").sheet1
    except Exception as e:
        print(f"Could not open Google Sheet. Check the name and sharing permissions. Error: {e}")
        return
        
    existing_links = sheet.col_values(6) # Column F holds the listing URLs
    new_data = get_ottawa_builds()
    
    added_count = 0
    for row in new_data:
        link = row[5]
        if link not in existing_links:
            sheet.append_row(row)
            added_count += 1
            
    print(f"Sync complete. Successfully added {added_count} new home listings to the sheet.")

if __name__ == "__main__":
    sync_to_sheets()
