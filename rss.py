import re
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright


WEB_URL = "https://www.tubosreunidosgroup.com/es/noticias"
BASE_URL = "https://www.tubosreunidosgroup.com"
ARCHIVO_RSS = Path("tubos-reunidos.xml")


def limpiar_texto(texto):
    return re.sub(r"\s+", " ", texto or "").strip()


def escapar_xml(texto):
    return (
        str(texto)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def convertir_fecha(texto):
    coincidencia = re.search(
        r"\b(\d{4})-(\d{2})-(\d{2})\b",
        texto,
    )

    if coincidencia:
        anio = int(coincidencia.group(1))
        mes = int(coincidencia.group(2))
        dia = int(coincidencia.group(3))

        try:
            return datetime(
                anio,
                mes,
                dia,
                12,
                0,
                0,
                tzinfo=timezone.utc,
            )
        except ValueError:
            return None

    coincidencia = re.search(
        r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b",
        texto,
    )

    if coincidencia:
        dia = int(coincidencia.group(1))
        mes = int(coincidencia.group(2))
        anio = int(coincidencia.group(3))

        try:
            return datetime(
                anio,
                mes,
                dia,
                12,
                0,
                0,
                tzinfo=timezone.utc,
            )
        except ValueError:
            return None

    return None


def es_enlace_noticia(url):
    ruta = urlparse(url).path.rstrip("/")

    # Formato utilizado por las noticias:
    # /es/noticias/2021/titulo-de-la-noticia-350
    return bool(
        re.match(
            r"^/es/noticias/\d{4}/[^/]+$",
            ruta,
            flags=re.IGNORECASE,
        )
    )


def obtener_noticias():
    with sync_playwright() as playwright:
        navegador = playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )

        contexto = navegador.new_context(
            locale="es-ES",
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )

        pagina = contexto.new_page()

        pagina.goto(
            WEB_URL,
            wait_until="domcontentloaded",
            timeout=90000,
        )

        # Espera hasta que JavaScript cargue noticias reales.
        pagina.wait_for_function(
            """
            () => Array.from(
                document.querySelectorAll("a[href]")
            ).some(
                enlace =>
                    /\\/es\\/noticias\\/\\d{4}\\//i.test(
                        enlace.href
                    )
            )
            """,
            timeout=60000,
        )

        pagina.wait_for_timeout(3000)

        resultados = pagina.locator(
            'a[href*="/es/noticias/"]'
        ).evaluate_all(
            """
            enlaces => enlaces.map(enlace => {
                let contenedor = enlace;

                for (let i = 0; i < 9; i++) {
                    if (!contenedor.parentElement) {
                        break;
                    }

                    contenedor = contenedor.parentElement;

                    const texto = (
                        contenedor.innerText || ""
                    ).replace(/\\s+/g, " ").trim();

                    if (
                        /\\b\\d{4}-\\d{2}-\\d{2}\\b/.test(texto) ||
                        /\\b\\d{1,2}\\/\\d{1,2}\\/\\d{4}\\b/.test(texto)
                    ) {
                        break;
                    }
                }

                const encabezado = contenedor.querySelector(
                    "h1, h2, h3, h4, h5, h6"
                );

                return {
                    url: enlace.href,
                    titulo: (
                        encabezado?.innerText ||
                        enlace.innerText ||
                        ""
                    ).replace(/\\s+/g, " ").trim(),
                    texto: (
                        contenedor.innerText || ""
                    ).replace(/\\s+/g, " ").trim()
                };
            })
            """
        )

        navegador.close()

    noticias = []
    enlaces_vistos = set()

    for resultado in resultados:
        url = urljoin(
            BASE_URL,
            resultado.get("url", ""),
        )
        url = url.split("#")[0].split("?")[0].rstrip("/")

        if not es_enlace_noticia(url):
            continue

        if url in enlaces_vistos:
            continue

        titulo = limpiar_texto(
            resultado.get("titulo", "")
        )
        texto = limpiar_texto(
            resultado.get("texto", "")
        )

        if titulo.lower() in {
            "descubre más",
            "descubre mas",
            "leer más",
            "leer mas",
            "ver más",
            "ver mas",
        }:
            continue

        if len(titulo) < 15:
            continue

        fecha = convertir_fecha(texto)

        if fecha is None:
            continue

        descripcion = texto.replace(
            titulo,
            " ",
        )

        descripcion = re.sub(
            r"\b\d{4}-\d{2}-\d{2}\b",
            " ",
            descripcion,
        )

        descripcion = re.sub(
            r"\b\d{1,2}/\d{1,2}/\d{4}\b",
            " ",
            descripcion,
        )

        descripcion = re.sub(
            r"\bdescubre\s+m[aá]s\b",
            " ",
            descripcion,
            flags=re.IGNORECASE,
        )

        descripcion = limpiar_texto(
            descripcion
        )[:1000]

        noticias.append(
            {
                "titulo": titulo,
                "url": url,
                "fecha": fecha,
                "descripcion": descripcion,
            }
        )

        enlaces_vistos.add(url)

    noticias.sort(
        key=lambda noticia: noticia["fecha"],
        reverse=True,
    )

    if not noticias:
        raise RuntimeError(
            "No se encontraron noticias de Tubos Reunidos"
        )

    return noticias[:50]


def crear_rss(noticias):
    ahora = datetime.now(timezone.utc)

    partes = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "<channel>",
        (
            "<title>Tubos Reunidos Group - "
            "Noticias</title>"
        ),
        f"<link>{escapar_xml(WEB_URL)}</link>",
        (
            "<description>Últimas noticias oficiales "
            "de Tubos Reunidos Group</description>"
        ),
        "<language>es</language>",
        f"<lastBuildDate>{format_datetime(ahora)}</lastBuildDate>",
        "<ttl>60</ttl>",
    ]

    for noticia in noticias:
        partes.extend(
            [
                "<item>",
                f"<title>{escapar_xml(noticia['titulo'])}</title>",
                f"<link>{escapar_xml(noticia['url'])}</link>",
                (
                    f'<guid isPermaLink="true">'
                    f"{escapar_xml(noticia['url'])}</guid>"
                ),
                (
                    f"<pubDate>"
                    f"{format_datetime(noticia['fecha'])}"
                    f"</pubDate>"
                ),
                (
                    f"<description>"
                    f"{escapar_xml(noticia['descripcion'])}"
                    f"</description>"
                ),
                "</item>",
            ]
        )

    partes.extend(
        [
            "</channel>",
            "</rss>",
        ]
    )

    return "\n".join(partes)


def guardar_rss(contenido):
    archivo_temporal = ARCHIVO_RSS.with_suffix(
        ".xml.tmp"
    )

    archivo_temporal.write_text(
        contenido,
        encoding="utf-8",
    )

    archivo_temporal.replace(
        ARCHIVO_RSS
    )


def main():
    noticias = obtener_noticias()
    contenido = crear_rss(noticias)
    guardar_rss(contenido)

    print(
        f"RSS de Tubos Reunidos creada con "
        f"{len(noticias)} noticias"
    )

    for noticia in noticias[:5]:
        print(
            noticia["fecha"].strftime("%d/%m/%Y"),
            "-",
            noticia["titulo"],
        )


if __name__ == "__main__":
    main()
