"""
HTML -> PDF con Chromium headless (Playwright).

Renderiza el HTML premium del memorando/ficha con el mismo motor de un navegador,
así el PDF queda idéntico a lo que se ve en pantalla (fuentes Playfair, grid,
logo SVG, acentos institucionales).

Se usa la API SÍNCRONA de Playwright; por eso los endpoints que la llaman deben
ser funciones `def` (no `async def`): FastAPI las corre en un threadpool sin event
loop, que es donde la API síncrona de Playwright puede operar.
"""
from __future__ import annotations


def html_to_pdf(html: str) -> bytes:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="networkidle")
            # Asegurar que las webfonts (Playfair / Source Sans) terminen de cargar.
            try:
                page.evaluate("() => document.fonts && document.fonts.ready")
            except Exception:
                pass
            pdf = page.pdf(
                format="Letter",
                print_background=True,
                prefer_css_page_size=True,   # respeta @page de la spec
            )
            return pdf
        finally:
            browser.close()