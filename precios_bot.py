from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup
import time
import os
import re
import json
from datetime import datetime, timedelta, timezone

URL_ARGENTINA = "https://www.naturacosmeticos.com.ar/c/todos-productos"
JSON_SALIDA = "productos_web.json"
SKUS_FILE = "skus.txt"
TZ_ARGENTINA = timezone(timedelta(hours=-3))

def ahora_argentina():
    return datetime.now(TZ_ARGENTINA)

def crear_driver():
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    return webdriver.Chrome(options=opts)

def limpiar_precio(texto):
    if not texto:
        return None
    if "impuesto" in texto.lower() or "nacional" in texto.lower():
        return None
    limpio = re.sub(r'[^\d,.]', '', texto)
    if not limpio:
        return None
    limpio = limpio.replace('.', '').replace(',', '.')
    try:
        valor = float(limpio)
        if valor < 10:
            return None
        return valor
    except ValueError:
        return None

def extraer_codigo_de_url(url):
    url_limpia = url.split("?")[0]
    ultimo_segmento = url_limpia.rstrip("/").split("/")[-1]
    match = re.search(r'(NATARG-\d+|NATURA-\d+)', ultimo_segmento, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    matches = re.findall(r'(NATARG-\d+|NATURA-\d+)', url, re.IGNORECASE)
    if matches:
        return matches[-1].upper()
    return None

def cargar_skus_archivo():
    if not os.path.exists(SKUS_FILE):
        print(f"Archivo {SKUS_FILE} no encontrado. Se omite segunda pasada.")
        return []
    with open(SKUS_FILE, "r", encoding="utf-8") as f:
        skus = [line.strip().upper() for line in f if line.strip()]
    print(f"SKUs cargados desde {SKUS_FILE}: {len(skus)}")
    return skus

def extraer_precios_de_ficha(driver, url):
    """
    Visita la ficha individual de un producto y extrae precios.
    Este metodo es confiable porque la ficha siempre tiene la misma estructura:
    - Precio promo: span#product-price o span.text-xl
    - Precio lista (tachado): span con clase line-through
    - Sin descuento: solo aparece el precio promo, lista = promo
    """
    try:
        driver.get(url)
        time.sleep(4)
        soup = BeautifulSoup(driver.page_source, "html.parser")

        precio_lista = None
        precio_promo = None

        # 1. Buscar precio promo en #product-price (el mas confiable)
        div_precio = soup.find(id="product-price")
        if div_precio:
            # Primero intentar aria-label del contenedor padre
            contenedor = div_precio.find_parent(attrs={"aria-label": True})
            if contenedor:
                aria = contenedor.get("aria-label", "")
                if "Precio" in aria:
                    precio_promo = limpiar_precio(aria)

            # Si no, usar el texto del span directamente
            if precio_promo is None:
                texto_precio = div_precio.get_text(strip=True)
                precio_promo = limpiar_precio(texto_precio)

        # 2. Fallback: buscar span.text-xl con precio
        if precio_promo is None:
            for span in soup.find_all("span"):
                clases = " ".join(span.get("class", []))
                if "text-xl" in clases:
                    texto = span.get_text(strip=True)
                    if "$" in texto:
                        val = limpiar_precio(texto)
                        if val:
                            precio_promo = val
                            break

        # 3. Precio tachado (lista) - span con line-through
        for span in soup.find_all("span"):
            clases = " ".join(span.get("class", []))
            if "line-through" in clases:
                texto = span.get_text(strip=True)
                val = limpiar_precio(texto)
                if val and val > 0:
                    precio_lista = val
                    break

        # 4. Sin descuento: lista = promo
        if precio_lista is None and precio_promo is not None:
            precio_lista = precio_promo

        return precio_lista, precio_promo

    except Exception as e:
        print(f"    Error extrayendo precios de {url}: {e}")
        return None, None

# ─── Paso 1: Escaneo del listado (solo codigos y URLs) ───────────────────────

def escanear_listado(driver):
    print(f"Cargando {URL_ARGENTINA} ...")
    driver.get(URL_ARGENTINA)
    time.sleep(10)

    clics = 0
    while clics < 200:
        try:
            boton = driver.find_element(By.CSS_SELECTOR, '[data-testid="plp-load-more-button"], [data-testid="product-list-load-more"]')
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", boton)
            time.sleep(1)
            driver.execute_script("arguments[0].click();", boton)
            clics += 1
            print(f"  Clic {clics}...")
            time.sleep(4)
        except:
            time.sleep(5)
            try:
                boton = driver.find_element(By.CSS_SELECTOR, '[data-testid="plp-load-more-button"], [data-testid="product-list-load-more"]')
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", boton)
                time.sleep(1)
                driver.execute_script("arguments[0].click();", boton)
                clics += 1
                print(f"  Clic {clics} (reintento)...")
                time.sleep(4)
            except:
                print(f"  Boton desaparecio. Fin de productos.")
                break

    print(f"Carga completa: {clics} clics.")

    soup = BeautifulSoup(driver.page_source, "html.parser")
    enlaces = soup.find_all("a", href=lambda x: x and "/p/" in x)

    productos = {}
    for a_tag in enlaces:
        href = a_tag.get("href", "")
        if not href.startswith("http"):
            href = "https://www.naturacosmeticos.com.ar" + href
        codigo = extraer_codigo_de_url(href)
        if not codigo or codigo in productos:
            continue
        productos[codigo] = href

    print(f"Productos unicos en listado: {len(productos)}")
    return productos

# ─── Paso 2: Visitar cada producto para precios exactos ──────────────────────

def obtener_precios_todos(driver, productos_listado, skus_archivo):
    todos = dict(productos_listado)

    faltantes = 0
    for sku in skus_archivo:
        if sku not in todos:
            todos[sku] = f"https://www.naturacosmeticos.com.ar/{sku}?_q={sku}&map=ft"
            faltantes += 1

    print(f"\nTotal SKUs a verificar precios: {len(todos)}")
    print(f"  Del listado: {len(productos_listado)}")
    print(f"  Extras del archivo skus.txt: {faltantes}")

    resultados = []
    total = len(todos)

    for i, (codigo, url) in enumerate(todos.items(), 1):
        if i % 50 == 0 or i <= 3 or i == total:
            print(f"  [{i}/{total}] {codigo}...")

        precio_lista, precio_promo = extraer_precios_de_ficha(driver, url)

        # Si es URL de busqueda, verificar que encontro el producto correcto
        if "?_q=" in url and precio_promo is None:
            soup = BeautifulSoup(driver.page_source, "html.parser")
            enlace = soup.find("a", href=lambda x: x and "/p/" in x and codigo.lower() in x.lower())
            if enlace:
                href = enlace.get("href", "")
                if not href.startswith("http"):
                    href = "https://www.naturacosmeticos.com.ar" + href
                precio_lista, precio_promo = extraer_precios_de_ficha(driver, href)
                url = href

        resultados.append({
            "codigo": codigo,
            "precio_lista_web": precio_lista,
            "precio_promo_web": precio_promo,
            "url": url,
        })

        time.sleep(1)

    return resultados

# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print(f"Natura Precios Bot - {ahora_argentina().strftime('%d/%m/%Y %H:%M:%S')}")
    print("=" * 60)

    skus_archivo = cargar_skus_archivo()

    driver = crear_driver()
    try:
        print("\n--- PASO 1: Escaneando listado ---")
        productos_listado = escanear_listado(driver)

        print("\n--- PASO 2: Obteniendo precios de cada producto ---")
        productos = obtener_precios_todos(driver, productos_listado, skus_archivo)

    finally:
        driver.quit()

    datos = {
        "productos": productos,
        "fecha_generado": ahora_argentina().isoformat(),
        "total": len(productos),
    }

    with open(JSON_SALIDA, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)

    print(f"\nJSON guardado: {JSON_SALIDA} ({len(productos)} productos)")
    print("Bot finalizado.")


if __name__ == "__main__":
    main()
