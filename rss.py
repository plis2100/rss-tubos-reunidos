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
    ruta = urlparse(url).path.lower().rstrip("/")

    if "/es/noticias/" not in ruta:
        return False

    if ruta in {
        "/es/noticias",
        "/es/noticias/",
    }:
        return False

    partes = [
        parte
        for parte in ruta.split("/")
        if parte
    ]

    # Debe tener más elementos que /es/noticias
    return len(partes) >= 4


def obtener_lineas(texto):
    lineas = []

    for linea in (texto or "").splitlines():
        linea = limpiar_texto(linea)

        if not linea:
            continue

        if linea not in lineas:
            lineas.append(linea)

    return lineas


def obtener_titulo_descripcion(texto):
    lineas = obtener_lineas(texto)
    candidatas = []

    for linea in lineas:
        linea_minuscula = linea.lower()

        if convertir_fecha(linea) is not None:
            continue

        if linea_minuscula in {
            "descubre más",
            "descubre mas",
            "leer más",
            "leer mas",
            "ver más",
            "ver mas",
            "noticias",
            "noticias y eventos",
        }:
            continue

        if "{{" in linea or "}}" in linea:
            continue

        candidatas.append(linea)

    if not candidatas:
        return "", ""

    # En las tarjetas, la primera línea útil es el título.
    titulo = candidatas[0]

    descripcion_partes = [
        linea
        for linea in candidatas[1:]
        if linea != titulo
    ]

    descripcion = limpiar_texto(
        " ".join(descripcion_partes)
    )[:1200]

    return titulo, descripcion


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

        # Espera a que Angular sustituya las variables
        # {{item.fecha}} por las noticias reales.
        pagina.wait_for_function(
            """
            () => {
                const texto = document.body.innerText || "";

                return (
                    !texto.includes("{{item.fecha}}") &&
                    (
                        /\\b\\d{4}-\\d{2}-\\d{2}\\b/.test(texto) ||
                        /\\b\\d{1,2}\\/\\d{1,2}\\/\\d{4}\\b/.test(texto)
                    )
                );
            }
            """,
            timeout=90000,
        )

        pagina.wait_for_timeout(5000)

        resultados = pagina.locator(
            'a[href*="/noticias/"]'
        ).evaluate_all(
            """
            enlaces => enlaces.map(enlace => {
                let contenedor = enlace;
                let encontrado = false;

                for (let i = 0; i < 10; i++) {
                    const texto = contenedor.innerText || "";

                    if (
                        /\\b\\d{4}-\\d{2}-\\d{2}\\b/.test(texto) ||
                        /\\b\\d{1,2}\\/\\d{1,2}\\/\\d{4}\\b/.test(texto)
                    ) {
                        encontrado = true;
                        break;
                    }

                    if (!contenedor.parentElement) {
                        break;
                    }

                    contenedor = contenedor.parentElement;
                }

                return {
                    url: enlace.href || "",
                    texto: encontrado
                        ? (contenedor.innerText || "")
                        : ""
                };
            })
            """
        )

        # Respaldo: obtiene cualquier elemento que contenga
        # una fecha y un enlace de noticia.
        if not resultados:
            resultados = pagina.locator(
                "article, li, .item, .card, .news-item"
            ).evaluate_all(
                """
                elementos => elementos.map(elemento => {
                    const enlace = elemento.querySelector(
                        'a[href*="/noticias/"]'
                    );

                    return {
                        url: enlace?.href || "",
                        texto: elemento.innerText || ""
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

        texto = resultado.get("texto", "")

        if not texto:
            continue

        fecha = convertir_fecha(texto)

        if fecha is None:
            continue

        titulo, descripcion = obtener_titulo_descripcion(
            texto
        )

        if len(titulo) < 15:
            continue

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

    for noticia in noticias[:10]:
        print(
            noticia["fecha"].strftime("%d/%m/%Y"),
            "-",
            noticia["titulo"],
        )


if __name__ == "__main__":
    main()
