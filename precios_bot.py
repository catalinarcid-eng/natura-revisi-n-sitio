from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup
import time
import os
import re
import json
from datetime import datetime, timedelta, timezone

# ─── Configuración ───────────────────────────────────────────────────────────
URL_ARGENTINA = "https://www.naturacosmeticos.com.ar/c/todos-productos"
URL_BUSQUEDA = "https://www.naturacosmeticos.com.ar/{}?_q={}&map=ft"
JSON_SALIDA = "productos_web.json"
SKUS_FILE = "skus.txt"

TZ_ARGENTINA = timezone(timedelta(hours=-3))

def ahora_argentina() -> datetime:
    return datetime.now(TZ_ARGENTINA)

# ─── Selenium ────────────────────────────────────────────────────────────────

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

def limpiar_precio(texto: str) -> float:
    if not texto:
        return None
    limpio = re.sub(r'[^\d,.]', '', texto)
    if not limpio:
        return None
    limpio = limpio.replace('.', '').replace(',', '.')
    try:
        return float(limpio)
    except ValueError:
        return None

def extraer_codigo_de_url(url: str) -> str:
    url_limpia = url.split("?")[0]
    ultimo_segmento = url_limpia.rstrip("/").split("/")[-1]
    match = re.search(r'(NATARG-\d+)', ultimo_segmento, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    matches = re.findall(r'(NATARG-\d+)', url, re.IGNORECASE)
    if matches:
        return matches[-1].upper()
    return None

def contar_productos_en_pagina(driver) -> int:
    return len(driver.find_elements(By.CSS_SELECTOR, 'a[href*="/p/"]'))

def cargar_skus_archivo() -> list:
    """Lee la lista de SKUs del archivo skus.txt (uno por línea)."""
    if not os.path.exists(SKUS_FILE):
        print(f"Archivo {SKUS_FILE} no encontrado. Se omite segunda pasada.")
        return []
    with open(SKUS_FILE, "r", encoding="utf-8") as f:
        skus = [line.strip().upper() for line in f if line.strip()]
    print(f"SKUs cargados desde {SKUS_FILE}: {len(skus)}")
    return skus

# ─── Paso 1: Escaneo del listado general ─────────────────────────────────────

def escanear_productos(driver) -> list:
    print(f"Cargando {URL_ARGENTINA} ...")
    driver.get(URL_ARGENTINA)
    time.sleep(10)

    productos_antes = contar_productos_en_pagina(driver)
    print(f"  Productos iniciales: {productos_antes}")

    clics = 0
    while clics < 200:
        try:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)

            botones = driver.find_elements(By.CSS_SELECTOR, '[data-testid="product-list-load-more"]')
            if not botones:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(3)
                botones = driver.find_elements(By.CSS_SELECTOR, '[data-testid="product-list-load-more"]')
                if not botones:
                    print(f"  Boton no encontrado. Fin de productos.")
                    break

            boton = botones[0]
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", boton)
            time.sleep(1)
            driver.execute_script("arguments[0].click();", boton)
            clics += 1
            time.sleep(4)

            productos_ahora = contar_productos_en_pagina(driver)
            nuevos = productos_ahora - productos_antes
            if nuevos > 0:
                print(f"  Clic {clics}: +{nuevos} productos (total: {productos_ahora})")
                productos_antes = productos_ahora
            else:
                print(f"  Clic {clics}: esperando carga...")
                time.sleep(3)

        except Exception as e:
            print(f"  Error en clic: {e}")
            break

    total_final = contar_productos_en_pagina(driver)
    print(f"Carga completa: {clics} clics, {total_final} productos en pagina.")

    soup = BeautifulSoup(driver.page_source, "html.parser")
    productos = []
    enlaces = soup.find_all("a", href=lambda x: x and "/p/" in x)

    vistos = set()
    for a_tag in enlaces:
        href = a_tag.get("href", "")
        if not href.startswith("http"):
            href = "https://www.naturacosmeticos.com.ar" + href

        codigo = extraer_codigo_de_url(href)
        if not codigo or codigo in vistos:
            continue
        vistos.add(codigo)

        card = a_tag.parent
        precio_lista = None
        precio_promo = None

        for _ in range(6):
            if not card:
                break
            tachado = card.find(class_=lambda c: c and "line-through" in c)
            if tachado and precio_lista is None:
                precio_lista = limpiar_precio(tachado.get_text())

            if precio_promo is None:
                for span in card.find_all("span"):
                    clases = span.get("class", [])
                    if "line-through" in clases:
                        continue
                    texto = span.get_text(strip=True)
                    if texto.startswith("$") and re.search(r'\d', texto):
                        precio_promo = limpiar_precio(texto)
                        break

            if precio_lista is not None and precio_promo is not None:
                break
            card = card.parent

        # Si no hay precio tachado (sin descuento), lista = promo
        if precio_lista is None and precio_promo is not None:
            precio_lista = precio_promo

        productos.append({
            "codigo": codigo,
            "precio_lista_web": precio_lista,
            "precio_promo_web": precio_promo,
            "url": href,
        })

    print(f"Productos extraidos del listado: {len(productos)}")
    return productos

# ─── Paso 2: Buscar productos faltantes uno por uno ──────────────────────────

def buscar_producto_individual(driver, sku: str) -> dict:
    """
    Busca un SKU directamente en la web de Natura visitando su ficha.
    Retorna dict con precio_lista_web, precio_promo_web, url, o None si no lo encuentra.
    """
    url_busqueda = f"https://www.naturacosmeticos.com.ar/{sku}?_q={sku}&map=ft"

    try:
        driver.get(url_busqueda)
        time.sleep(5)

        # Verificar si llegamos a una ficha de producto o a resultados de búsqueda
        url_actual = driver.current_url
        soup = BeautifulSoup(driver.page_source, "html.parser")

        # Verificar si el SKU aparece en la página
        texto_pagina = soup.get_text(separator=" ").upper()
        if sku not in texto_pagina:
            # Intentar buscar en resultados de búsqueda
            enlaces_producto = soup.find_all("a", href=lambda x: x and "/p/" in x and sku.lower() in x.lower())
            if enlaces_producto:
                href = enlaces_producto[0].get("href", "")
                if not href.startswith("http"):
                    href = "https://www.naturacosmeticos.com.ar" + href
                driver.get(href)
                time.sleep(4)
                soup = BeautifulSoup(driver.page_source, "html.parser")
            else:
                return None

        # Extraer precios de la ficha de producto
        precio_lista = None
        precio_promo = None

        # Precio tachado (de lista)
        tachado = soup.find(class_=lambda c: c and "line-through" in c)
        if tachado:
            precio_lista = limpiar_precio(tachado.get_text())

        # Precio promo: buscar en el div#product-price
        div_precio = soup.find(id="product-price")
        if div_precio:
            for span in div_precio.find_all("span"):
                clases = " ".join(span.get("class", []))
                if "line-through" in clases:
                    continue
                texto = span.get_text(strip=True)
                if texto.startswith("$") and re.search(r'\d', texto):
                    val = limpiar_precio(texto)
                    if val and (precio_promo is None or val < precio_promo):
                        precio_promo = val

        # Fallback: buscar cualquier span con precio que no sea tachado
        if precio_promo is None:
            for span in soup.find_all("span", class_="text-xl"):
                texto = span.get_text(strip=True)
                if texto.startswith("$"):
                    precio_promo = limpiar_precio(texto)
                    break

        # Si no hay precio tachado (sin descuento), lista = promo
        if precio_lista is None and precio_promo is not None:
            precio_lista = precio_promo

        return {
            "codigo": sku,
            "precio_lista_web": precio_lista,
            "precio_promo_web": precio_promo,
            "url": driver.current_url,
        }

    except Exception as e:
        print(f"    Error buscando {sku}: {e}")
        return None

# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print(f"Natura Precios Bot - {ahora_argentina().strftime('%d/%m/%Y %H:%M:%S')}")
    print("=" * 60)

    # Cargar lista de SKUs del archivo (para segunda pasada)
    skus_archivo = cargar_skus_archivo()

    driver = crear_driver()
    try:
        # PASO 1: Escanear el listado general
        productos = escanear_productos(driver)

        # Identificar SKUs encontrados
        codigos_encontrados = set(p["codigo"] for p in productos if p["codigo"])

        # PASO 2: Buscar productos faltantes
        if skus_archivo:
            faltantes = [sku for sku in skus_archivo if sku not in codigos_encontrados]
            print(f"\nSKUs en tu lista: {len(skus_archivo)}")
            print(f"Encontrados en listado: {len(codigos_encontrados)}")
            print(f"Faltantes a buscar individualmente: {len(faltantes)}")

            for i, sku in enumerate(faltantes, 1):
                print(f"  Buscando {sku} ({i}/{len(faltantes)})...")
                resultado = buscar_producto_individual(driver, sku)
                if resultado:
                    productos.append(resultado)
                    print(f"    Encontrado: lista={resultado['precio_lista_web']} promo={resultado['precio_promo_web']}")
                else:
                    print(f"    No encontrado en la web.")
                time.sleep(2)

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
