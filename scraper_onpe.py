import os
import json
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from playwright.sync_api import sync_playwright

URL_ONPE = "https://reclutamiento.onpe.gob.pe/convocatorias"
STATE_FILE = "estado_previo.json"

# Zonas prioritarias (Trujillo y distritos)
ZONAS_TRUJILLO = [
    "TRUJILLO",
    "LA ESPERANZA",
    "VICTOR LARCO HERRERA"
]

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASS = os.getenv("GMAIL_APP_PASS")
EMAIL_DESTINO = os.getenv("EMAIL_DESTINO")


def enviar_correo(asunto, html_cuerpo):
    if not GMAIL_USER or not GMAIL_APP_PASS or not EMAIL_DESTINO:
        print("[!] Faltan credenciales de Gmail.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"] = f"Monitor ONPE <{GMAIL_USER}>"
    msg["To"] = EMAIL_DESTINO
    msg.attach(MIMEText(html_cuerpo, "html"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(GMAIL_USER, GMAIL_APP_PASS)
            server.send_message(msg)
        print(f"[+] Correo enviado a {EMAIL_DESTINO}")
    except Exception as e:
        print(f"[-] Error enviando correo: {e}")


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
    print(f"[+] Estado guardado exitosamente en {STATE_FILE}")


def extraer_convocatorias():
    datos = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        print(f"[*] Navegando a {URL_ONPE}...")
        try:
            page.goto(URL_ONPE, wait_until="networkidle", timeout=60000)
        except Exception as e:
            print(f"[i] Aviso de carga: {e}")

        # Espera activa hasta que Angular dibuje las tarjetas
        try:
            print("[*] Esperando a que Angular renderice las ofertas...")
            page.wait_for_selector(".card-normal", timeout=35000)
        except Exception:
            print("[!] No se encontró '.card-normal'. Diagnóstico de lo que ve el navegador:")
            print(f"URL actual: {page.url}")
            print(f"Título: {page.title()}")
            print(f"Texto visible:\n{page.locator('body').inner_text()[:600]}")
            browser.close()
            return datos

        tarjetas = page.locator(".card-normal")
        total = tarjetas.count()
        print(f"[*] ¡Convocatorias detectadas con éxito!: {total}")

        if total == 0:
            browser.close()
            return datos

        # Guardamos títulos
        titulos = []
        for i in range(total):
            t = tarjetas.nth(i).locator("h2").first.inner_text().strip()
            titulos.append(t if t else f"Puesto #{i+1}")

        # Analizamos las sedes de cada una
        for idx, puesto in enumerate(titulos):
            print(f"[{idx+1}/{total}] Analizando sedes de: {puesto}...")
            try:
                # Si no estamos en la vista de tarjetas, volvemos
                if page.locator(".card-normal").count() == 0:
                    page.goto(URL_ONPE, wait_until="networkidle", timeout=45000)
                    page.wait_for_selector(".card-normal", timeout=25000)

                card_actual = page.locator(".card-normal").nth(idx)
                btn_detalle = card_actual.locator("button.button-none, button:has-text('Ver Detalle')")

                if btn_detalle.count() > 0:
                    btn_detalle.first.click()
                else:
                    card_actual.click()

                # Esperar a que cargue la lista de sedes
                page.wait_for_selector("li", timeout=15000)
                page.wait_for_timeout(1000)

                items_li = page.locator("li").all_inner_texts()
                sedes_validas = [li.strip() for li in items_li if "Cantidad requerida:" in li or "Plazo para postulación:" in li]

                # Filtrar si alguna es de Trujillo
                zonas_encontradas = []
                for s in sedes_validas:
                    for z in ZONAS_TRUJILLO:
                        if re.search(rf"\b{re.escape(z)}\b", s.upper()):
                            zonas_encontradas.append(s)
                            break

                datos[puesto] = {
                    "total_sedes": len(sedes_validas),
                    "sedes": sedes_validas,
                    "sedes_trujillo": zonas_encontradas
                }

                # Volver a la lista de ofertas
                page.goto(URL_ONPE, wait_until="networkidle", timeout=45000)
                page.wait_for_selector(".card-normal", timeout=25000)

            except Exception as err:
                print(f"[-] Error analizando {puesto}: {err}")
                continue

        browser.close()
    return datos


def procesar_cambios(datos_actuales):
    if not datos_actuales:
        print("[!] No se obtuvieron datos en esta vuelta.")
        return

    estado_previo = cargar_estado()

    # 1. Primer escaneo exitoso
    if not estado_previo:
        print("[+] Guardando estado inicial...")
        guardar_estado(datos_actuales)

        trujillo_ahora = []
        for puesto, val in datos_actuales.items():
            if val["sedes_trujillo"]:
                trujillo_ahora.append((puesto, val["sedes_trujillo"]))

        if trujillo_ahora:
            asunto = "🚨 [ONPE TRUJILLO] ¡Plazas detectadas en tu zona ahora mismo!"
            items_html = ""
            for p, sedes in trujillo_ahora:
                items_html += f"<h3>{p}</h3><ul>"
                for s in sedes:
                    items_html += f"<li><b>{s}</b></li>"
                items_html += "</ul>"

            cuerpo = f"""
            <html><body style="font-family: Arial, sans-serif;">
            <h2 style="color: #d9534f;">¡Atención! Se detectaron plazas en Trujillo:</h2>
            {items_html}
            <br>
            <a href="{URL_ONPE}" style="background-color: #d9534f; color: white; padding: 12px 20px; text-decoration: none; border-radius: 5px; font-weight: bold;">
                POSTULAR AQUÍ (ONPE)
            </a>
            </body></html>
            """
            enviar_correo(asunto, cuerpo)
        else:
            asunto = f"✅ Monitor ONPE Activo: {len(datos_actuales)} Ofertas Monitoreadas"
            resumen_puestos = "".join([f"<li><b>{p}</b> ({val['total_sedes']} sedes a nivel nacional)</li>" for p, val in datos_actuales.items()])
            cuerpo = f"""
            <html><body style="font-family: Arial, sans-serif;">
            <h3 style="color: #0275d8;">El monitor está funcionando correctamente.</h3>
            <p>Se están vigilando las <b>{len(datos_actuales)} ofertas laborales</b> cada 30 minutos:</p>
            <ul>{resumen_puestos}</ul>
            <p><i>(Ninguna tiene sede abierta para Trujillo en este momento. Te avisaremos apenas aparezca una).</i></p>
            <br>
            <a href="{URL_ONPE}" style="background-color: #0275d8; color: white; padding: 10px 18px; text-decoration: none; border-radius: 4px;">
                Ver Portal ONPE
            </a>
            </body></html>
            """
            enviar_correo(asunto, cuerpo)
        return

    # 2. Detección de cambios
    hay_cambios = False
    alertas_trujillo = []
    nuevas_generales = []

    for puesto, val in datos_actuales.items():
        if val["sedes_trujillo"]:
            sedes_antiguas = estado_previo.get(puesto, {}).get("sedes_trujillo", [])
            nuevas_sedes_trujillo = [s for s in val["sedes_trujillo"] if s not in sedes_antiguas]
            if nuevas_sedes_trujillo:
                alertas_trujillo.append((puesto, nuevas_sedes_trujillo))
                hay_cambios = True

        if puesto not in estado_previo:
            nuevas_generales.append(puesto)
            hay_cambios = True

    if alertas_trujillo:
        asunto = "🚨🚨 [URGENTE ONPE] ¡PLAZA DISPONIBLE EN TRUJILLO! 🚨🚨"
        detalles_html = ""
        for p, sedes in alertas_trujillo:
            detalles_html += f"""
            <div style="border-left: 4px solid #d9534f; padding-left: 10px; margin-bottom: 15px;">
                <h3 style="color: #d9534f; margin: 0;">{p}</h3>
                <ul>{"".join([f"<li><b>{s}</b></li>" for s in sedes])}</ul>
            </div>
            """

        cuerpo = f"""
        <html><body style="font-family: Arial, sans-serif;">
            <h2 style="color: #c9302c;">¡Nueva Convocatoria Disponible en tu Zona!</h2>
            {detalles_html}
            <br>
            <a href="{URL_ONPE}" style="background-color: #d9534f; color: white; padding: 12px 20px; text-decoration: none; font-weight: bold; border-radius: 4px; display: inline-block;">
                POSTULAR DE INMEDIATO
            </a>
        </body></html>
        """
        enviar_correo(asunto, cuerpo)

    elif nuevas_generales:
        asunto = "📢 [ONPE] Nueva(s) Oferta(s) Publicada(s)"
        lista_html = "".join([f"<li><b>{p}</b></li>" for p in nuevas_generales])
        cuerpo = f"""
        <html><body style="font-family: Arial, sans-serif;">
            <h3>Se publicaron nuevos puestos en la ONPE:</h3>
            <ul>{lista_html}</ul>
            <br>
            <a href="{URL_ONPE}" style="background-color: #0275d8; color: white; padding: 10px 18px; text-decoration: none; border-radius: 4px;">
                Ver en la ONPE
            </a>
        </body></html>
        """
        enviar_correo(asunto, cuerpo)
    else:
        print("[i] Revisión finalizada: Sin cambios.")

    if hay_cambios:
        guardar_estado(datos_actuales)


if __name__ == "__main__":
    datos = extraer_convocatorias()
    procesar_cambios(datos)
