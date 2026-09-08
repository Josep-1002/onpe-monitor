import os
import json
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from playwright.sync_api import sync_playwright

URL_ONPE = "https://reclutamiento.onpe.gob.pe/convocatorias"
STATE_FILE = "estado_previo.json"

# Zonas geográficas prioritarias
ZONAS_TRUJILLO = [
    "TRUJILLO",
    "LA ESPERANZA",
    "VICTOR LARCO",
    "VICTOR LARCO HERRERA",
    "EL PORVENIR",
    "FLORENCIA DE MORA",
    "MOCHE",
    "HUANCHACO",
    "LA LIBERTAD",
    "ODPE TRUJILLO"
]

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASS = os.getenv("GMAIL_APP_PASS")
EMAIL_DESTINO = os.getenv("EMAIL_DESTINO")


def enviar_correo(asunto, html_cuerpo):
    if not GMAIL_USER or not GMAIL_APP_PASS or not EMAIL_DESTINO:
        print("[!] Variables de entorno para Gmail no configuradas.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"] = f"Alerta ONPE <{GMAIL_USER}>"
    msg["To"] = EMAIL_DESTINO
    msg.attach(MIMEText(html_cuerpo, "html"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(GMAIL_USER, GMAIL_APP_PASS)
            server.send_message(msg)
        print(f"[+] Notificación enviada con éxito a {EMAIL_DESTINO}")
    except Exception as e:
        print(f"[-] Error al despachar el correo: {e}")


def cargar_estado():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def guardar_estado(data):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def extraer_convocatorias():
    datos = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        print(f"[*] Conectando a {URL_ONPE}...")
        try:
            page.goto(URL_ONPE, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(4000)
        except Exception as e:
            print(f"[-] Error de conexión: {e}")
            browser.close()
            return datos

        botones = page.locator("button, a, div[role='button'], .card, .oferta-item").all()
        elementos_validos = []
        for b in botones:
            txt = b.inner_text().strip()
            if txt and len(txt) > 5 and not any(ign in txt.lower() for ign in ["ingresar", "login", "inicio", "descargar", "cerrar"]):
                elementos_validos.append(b)

        print(f"[*] Elementos detectados: {len(elementos_validos)}")

        for idx, elem in enumerate(elementos_validos):
            try:
                titulo = elem.inner_text().split("\n")[0].strip()
                if not titulo:
                    titulo = f"Convocatoria #{idx+1}"

                elem.click(timeout=3000)
                page.wait_for_timeout(1500)

                texto_pagina = page.locator("body").inner_text()
                lineas = texto_pagina.split("\n")
                sedes_encontradas = []

                for lin in lineas:
                    lin_strip = lin.strip()
                    if any(term in lin_strip for term in ["Cantidad requerida:", "Plazo para postulación:"]):
                        sedes_encontradas.append(lin_strip)

                if not sedes_encontradas:
                    coincidencias = re.findall(r"([A-ZÁÉÍÓÚÑ\s]+)\s*\(Cantidad requerida:.*?\)", texto_pagina)
                    sedes_encontradas = [c.strip() for c in coincidencias if len(c.strip()) > 2]

                texto_mayusc = texto_pagina.upper()
                zonas_detectadas = [z for z in ZONAS_TRUJILLO if z in texto_mayusc]

                datos[titulo] = {
                    "sedes": sedes_encontradas,
                    "zonas_trujillo": zonas_detectadas,
                    "texto_resumen": texto_pagina[:3000]
                }

                cerrar = page.locator("button:has-text('Cerrar'), button.close, .modal-close")
                if cerrar.count() > 0:
                    cerrar.first.click()
                    page.wait_for_timeout(500)

            except Exception as e:
                print(f"[-] Saltando elemento {idx}: {e}")
                continue

        browser.close()
    return datos


def procesar_cambios(datos_actuales):
    if not datos_actuales:
        print("[!] Sin datos extraídos en este ciclo.")
        return

    estado_previo = cargar_estado()

    if not estado_previo:
        print("[+] Guardando estado base inicial...")
        guardar_estado(datos_actuales)

        trujillo_inicial = []
        for puesto, val in datos_actuales.items():
            if val["zonas_trujillo"]:
                trujillo_inicial.append((puesto, val["zonas_trujillo"]))

        if trujillo_inicial:
            asunto = "🚨 [ONPE TRUJILLO] Plazas detectadas en el primer análisis"
            cuerpo = f"""
            <h2>¡Atención! Plazas detectadas en tu zona:</h2>
            <ul>
            {"".join(f"<li><b>{p}</b>: Zonas detectadas -> {', '.join(z)}</li>" for p, z in trujillo_inicial)}
            </ul>
            <p><a href="{URL_ONPE}" style="background-color:#0056b3;color:white;padding:10px 15px;text-decoration:none;border-radius:5px;">Ir a Convocatorias ONPE</a></p>
            """
            enviar_correo(asunto, cuerpo)
        else:
            asunto = "✅ Monitor ONPE Activado en GitHub"
            cuerpo = f"""
            <h3>El monitor se ha iniciado con éxito.</h3>
            <p>Se registraron <b>{len(datos_actuales)}</b> convocatorias activas (ninguna en Trujillo por el momento).</p>
            <p>Recibirás un correo de inmediato cuando aparezca Trujillo o una nueva oferta.</p>
            """
            enviar_correo(asunto, cuerpo)
        return

    hay_cambios = False
    alertas_trujillo = []
    nuevas_generales = []

    for puesto, val in datos_actuales.items():
        zonas = val["zonas_trujillo"]

        # 1. Alerta prioritaria: Plaza en Trujillo no registrada antes
        if zonas:
            if puesto not in estado_previo or not estado_previo[puesto].get("zonas_trujillo"):
                alertas_trujillo.append((puesto, zonas, val["sedes"]))
                hay_cambios = True

        # 2. Nueva convocatoria en general
        if puesto not in estado_previo:
            nuevas_generales.append(puesto)
            hay_cambios = True

    if alertas_trujillo:
        asunto = "🚨🚨 [URGENTE ONPE] ¡PLAZA DISPONIBLE EN TRUJILLO / LA LIBERTAD! 🚨🚨"
        detalles_html = ""
        for p, z, s in alertas_trujillo:
            detalles_html += f"""
            <div style="border-left: 4px solid #d9534f; padding-left: 10px; margin-bottom: 15px;">
                <h3 style="color: #d9534f; margin: 0;">{p}</h3>
                <p><b>Zona detectada:</b> {", ".join(z)}</p>
                <p><b>Detalles / Sedes:</b><br>{'<br>'.join(s) if s else 'Revisar detalle en el portal'}</p>
            </div>
            """

        cuerpo = f"""
        <html>
        <body style="font-family: Arial, sans-serif;">
            <h2 style="color: #c9302c;">¡Nueva Convocatoria en tu localidad!</h2>
            {detalles_html}
            <br>
            <a href="{URL_ONPE}" style="background-color: #d9534f; color: white; padding: 12px 20px; text-decoration: none; font-weight: bold; border-radius: 4px; display: inline-block;">
                POSTULAR AQUÍ (ONPE)
            </a>
        </body>
        </html>
        """
        enviar_correo(asunto, cuerpo)

    elif nuevas_generales:
        asunto = "📢 [ONPE] Nueva(s) Oferta(s) Laboral(es) Publicada(s)"
        lista_html = "".join([f"<li><b>{p}</b></li>" for p in nuevas_generales])
        cuerpo = f"""
        <html>
        <body style="font-family: Arial, sans-serif;">
            <h3>Nuevas ofertas publicadas en la ONPE:</h3>
            <ul>{lista_html}</ul>
            <p><i>(Sedes en otras regiones del país)</i></p>
            <br>
            <a href="{URL_ONPE}" style="background-color: #0275d8; color: white; padding: 10px 18px; text-decoration: none; border-radius: 4px; display: inline-block;">
                Ver Convocatorias
            </a>
        </body>
        </html>
        """
        enviar_correo(asunto, cuerpo)
    else:
        print("[i] Revisión finalizada: Sin cambios.")

    if hay_cambios:
        guardar_estado(datos_actuales)


if __name__ == "__main__":
    datos = extraer_convocatorias()
    procesar_cambios(datos)
