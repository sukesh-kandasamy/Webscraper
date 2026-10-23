from bs4 import BeautifulSoup
import requests

URL = "https://www.imdb.com/chart/top/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}

try:
    response = requests.get(URL, headers=HEADERS, timeout=20)
    soup = BeautifulSoup(response.text, "html.parser")
    print(soup)
   

except requests.RequestException as e:
    print("Error occurred while fetching the webpage:", e)