"""Vigila los MacBook Air reacondicionados de Apple España y avisa por ntfy."""

import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

# ---------------- CONFIGURACIÓN (cámbiala aquí) ----------------
KEYWORD = "MacBook Air"   # se busca en el título, sin mayúsculas ni tildes
MAX_PRICE = 1500          # en euros; avisa solo si el precio es MENOR
# ----------------------------------------------------------------

URL = "https://www.apple.com/es/shop/refurbished/mac/macbook-air"
STATE_FILE = Path(__file__).with_name("seen.json")
TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}
# Precio en formato español: 1.239,00 €  (el primero que aparece es el actual)
PRICE_RE = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*€")


def normalize(text):
    """Minúsculas, sin tildes y con espacios simples."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().split())


def parse_price(text):
    return float(text.replace(".", "").replace(",", "."))


def find_price(link):
    """Sube desde el enlace hasta el bloque del producto y lee su primer precio."""
    node = link
    for _ in range(6):
        node = node.parent
        if node is None:
            return None
        # Si el bloque ya contiene otro producto, nos hemos pasado: no hay precio fiable.
        urls = {a["href"].split("?")[0] for a in node.select('a[href*="/shop/product/"]')}
        if len(urls) > 1:
            return None
        match = PRICE_RE.search(node.get_text(" ", strip=True))
        if match:
            return parse_price(match.group(1))
    return None


def scrape():
    resp = requests.get(URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    products = {}
    for link in soup.select('a[href*="/shop/product/"]'):
        title = " ".join(link.get_text(" ", strip=True).split())
        if not title:
            continue  # enlaces de imagen sin texto
        url = urljoin(URL, link["href"].split("?")[0].split("#")[0])
        if url not in products:
            products[url] = {"url": url, "title": title, "price": find_price(link)}
    return list(products.values())


def notify(title, message, click=None):
    """Envía a ntfy. Devuelve True si fue bien."""
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    headers = {"Title": ascii_title, "Tags": "computer"}
    if click:
        headers["Click"] = quote(click, safe=":/?=&%#")  # las cabeceras deben ser ASCII
    try:
        r = requests.post(
            f"https://ntfy.sh/{TOPIC}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"Error enviando a ntfy: {e}")
        return False


def euros(value):
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"seen": [], "empty_alerted": False}


def save_state(state):
    state["seen"] = sorted(state["seen"])
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main():
    state = load_state()
    products = scrape()
    print(f"Productos leídos: {len(products)}")

    if not products:
        if not state.get("empty_alerted"):
            notify("Monitor Apple: 0 productos leidos",
                   "No se ha leído ningún producto. La web de Apple puede haber cambiado.",
                   URL)
            state["empty_alerted"] = True
        save_state(state)  # se conserva "seen" para no repetir avisos cuando vuelva
        return

    state["empty_alerted"] = False
    key = normalize(KEYWORD)
    matches = [
        p for p in products
        if key in normalize(p["title"]) and p["price"] is not None and p["price"] < MAX_PRICE
    ]
    print(f"{KEYWORD} por debajo de {MAX_PRICE} €: {len(matches)}")

    seen = set(state.get("seen", []))
    new_seen = []
    for p in matches:
        if p["url"] in seen:
            new_seen.append(p["url"])
            continue
        print(f"NUEVO: {p['title']} - {euros(p['price'])}")
        if notify(f"{KEYWORD} por {euros(p['price'])[:-2]} EUR",
                  f"{p['title']}\n{euros(p['price'])}", p["url"]):
            new_seen.append(p["url"])  # si falla el aviso, se reintenta en la próxima ejecución

    state["seen"] = new_seen  # solo lo que sigue listado ahora
    save_state(state)


if __name__ == "__main__":
    if not TOPIC:
        sys.exit("Falta la variable de entorno NTFY_TOPIC (secret de GitHub).")
    main()
