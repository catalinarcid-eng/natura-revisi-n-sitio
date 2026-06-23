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
JSON_SALIDA = "productos_web.json"

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
    """
    Convierte un precio en formato argentino ('$ 52.845,00') a float (52845.00).
    Devuelve None si no se puede parsear.
    """
    if not texto:
        return None
    # Quitar todo lo que no sea número, punto o coma
    limpio = re.sub(r'[^\d,.]', '', texto)
    if not limpio:
        return None
    # Formato argentino: punto = miles, coma = decimales
    limpio = limpio.replace('.', '').replace(',', '.')
    try:
        return float(limpio)
    except ValueError:
        return None

def extraer_codigo_de_url(url: str) -> str:
    # El código real siempre está al final de la URL, después del último /
    # Ejemplo: /p/nombre-producto-nature-250-ml/NATARG-102396?position=...
    # Primero limpiar query params
    url_limpia = url.split("?")[0]
    # Tomar el último segmento de la URL
    ultimo_segmento = url_limpia.rstrip("/").split("/")[-1]
    match = re.search(r'(NATARG-\d+)', ultimo_segmento, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    # Fallback: buscar NATARG-XXXXX en toda la URL pero tomando el ÚLTIMO match
    matches = re.findall(r'(NATARG-\d+)', url, re.IGNORECASE)
    if matches:
        return matches[-1].upper()
    return None

def contar_productos_en_pagina(driver) -> int:
    """Cuenta cuántos productos hay actualmente visibles en la página."""
    return len(driver.find_elements(By.CSS_SELECTOR, 'a[href*="/p/"]'))

def escanear_productos(driver) -> list:
    """
    Carga todos los productos de la categoría y extrae:
    codigo, precio_lista (tachado), precio_promo (final), url
    """
    print(f"Cargando {URL_ARGENTINA} ...")
    driver.get(URL_ARGENTINA)
    time.sleep(10)

    productos_antes = contar_productos_en_pagina(driver)
    print(f"  Productos iniciales: {productos_antes}")

    clics = 0
    intentos_sin_nuevos = 0

    while clics < 600:
        try:
            # Scroll al fondo de la página primero
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)

            # Buscar el botón
            botones = driver.find_elements(By.CSS_SELECTOR, '[data-testid="product-list-load-more"]')
            if not botones:
                # No hay botón visible, intentar scroll extra por si no cargó
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(3)
                botones = driver.find_elements(By.CSS_SELECTOR, '[data-testid="product-list-load-more"]')
                if not botones:
                    print(f"  Botón no encontrado. Fin de productos.")
                    break

            boton = botones[0]
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", boton)
            time.sleep(1)
            driver.execute_script("arguments[0].click();", boton)
            clics += 1
            time.sleep(4)

            # Verificar si se cargaron productos nuevos
            productos_ahora = contar_productos_en_pagina(driver)
            nuevos = productos_ahora - productos_antes

            if nuevos > 0:
                print(f"  Clic {clics}: +{nuevos} productos (total: {productos_ahora})")
                productos_antes = productos_ahora
                intentos_sin_nuevos = 0
            else:
                intentos_sin_nuevos += 1
                print(f"  Clic {clics}: sin productos nuevos (intento {intentos_sin_nuevos}/3)")
                # Esperar más y reintentar
                time.sleep(3)
                if intentos_sin_nuevos >= 3:
                    print(f"  3 intentos sin productos nuevos. Fin de carga.")
                    break

        except Exception as e:
            print(f"  Error en clic: {e}")
            break

    # Scroll final al fondo para asegurar que todo cargó
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(3)

    total_final = contar_productos_en_pagina(driver)
    print(f"Carga completa: {clics} clics, {total_final} productos en pagina.")
    soup = BeautifulSoup(driver.page_source, "html.parser")

    productos = []
    # Cada tarjeta de producto tiene un <a href="/p/..."> con el link
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

        # Subir/bajar en el DOM para encontrar precios dentro de la tarjeta
        card = a_tag.parent
        precio_lista = None
        precio_promo = None

        for _ in range(6):
            if not card:
                break
            # Precio tachado (lista)
            tachado = card.find(class_=lambda c: c and "line-through" in c)
            if tachado and precio_lista is None:
                precio_lista = limpiar_precio(tachado.get_text())

            # Precio final - buscar spans con $ que no estén tachados
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

    print(f"Productos encontrados: {len(productos)}")
    return productos

# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print(f"Natura Precios Bot - {ahora_argentina().strftime('%d/%m/%Y %H:%M:%S')}")
    print("=" * 60)

    driver = crear_driver()
    try:
        productos = escanear_productos(driver)
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
