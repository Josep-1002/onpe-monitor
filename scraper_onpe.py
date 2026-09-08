import os
import json
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from playwright.sync_api import sync_playwright

URL_ONPE = "https://reclutamiento.onpe.gob.pe/convocatorias"
STATE_FILE = "estado_previo.json"

# Zonas prioritarias (Trujillo, distritos y La Libertad)
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
        print("[!] Faltan variables de entorno para enviar correo.")
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
        print(f"[+] Notificación enviada con éxito a {EMAIL_DESTINO}")
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
            page.goto(URL_ONPE, wait_until="networkidle", timeout=45000)
            page.wait_for_selector(".card-normal", timeout=20000)
        except Exception as e:
            print(f"[-] Error al cargar la página: {e}")
            browser.close()
            return datos

        total_ofertas = page.locator(".card-normal").count()
        print(f"[*] Total de ofertas laborales detectadas: {total_ofertas}")

        for i in range(total_ofertas):
            try:
                # Nos aseguramos de estar en la vista principal con las tarjetas
                if page.locator(".card-normal").count() == 0:
                    page.goto(URL_ONPE, wait_until="networkidle", timeout=30000)
                    page.wait_for_selector(".card-normal", timeout=15000)

                tarjetas = page.locator(".card-normal")
                tarjeta = tarjetas.nth(i)

                # Obtener el título del puesto
                titulo = tarjeta.locator("h2").first.inner_text().strip()
                print(f"[{i+1}/{total_ofertas}] Analizando: {titulo}...")

                # Hacer clic en el botón 'Ver Detalle'
                boton_detalle = tarjeta.locator("button.button-none")
                if boton_detalle.count() > 0:
                    boton_detalle.first.click()
                else:
                    tarjeta.click()

                # Esperar a que cargue la lista de sedes ODPE
                page.wait_for_timeout(2000)

                # Extraer todos los ítems de sedes con sus plazos
                sedes_raw = page.locator("li").all_inner_texts()
                sedes_encontradas = [s.strip() for s in sedes_raw if "Cantidad requerida:" in s or "Plazo para postulación:" in s]

                # Filtrar si alguna corresponde a Trujillo o distritos
                zonas_detectadas = []
                for s in sedes_encontradas:
                    for zona in ZONAS_TRUJILLO:
                        if re.search(rf"\b{re.escape(zona)}\b", s.upper()):
                            zonas_detectadas.append(s)

                datos[titulo] = {
                    "total_sedes": len(sedes_encontradas),
                    "sedes": sedes_encontradas,
                    "sedes_trujillo": zonas_detectadas
                }

                # Regresar a la lista de ofertas
                boton_volver = page.locator("button:has-text('Volver'), a:has-text('Volver'), button:has-text('Regresar')")
                if boton_volver.count() > 0 and boton_volver.first.is_visible():
                    boton_volver.first.click()
                    page.wait_for_timeout(1000)
                else:
                    page.go_back()
                    page.wait_for_timeout(1000)

            except Exception as err:
                print(f"[-] Error en oferta {i}: {err}")
                # Si falló la navegación, recargamos la URL principal
                try:
                    page.goto(URL_ONPE, wait_until="networkidle", timeout=30000)
                except Exception:
                    pass
                continue

        browser.close()
    return datos


def procesar_cambios(datos_actuales):
    if not datos_actuales:
        print("[!] No se extrajeron datos en este ciclo.")
        return

    estado_previo = cargar_estado()
    
    # 1. Si es la primera ejecución con este nuevo scraper
    if not estado_previo:
        print("[+] Guardando estado base...")
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
            <h2 style="color: #d9534f;">¡Atención! Se detectaron plazas en Trujillo / La Libertad:</h2>
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
            resumen_puestos = "".join([f"<li>{p} ({val['total_sedes']} sedes registradas)</li>" for p, val in datos_actuales.items()])
            cuerpo = f"""
            <html><body style="font-family: Arial, sans-serif;">
            <h3 style="color: #0275d8;">El monitor está funcionando correctamente.</h3>
            <p>Se están vigilando <b>{len(datos_actuales)} ofertas laborales</b> cada 30 minutos:</p>
            <ul>{resumen_puestos}</ul>
            <p><i>(Ninguna tiene sede habilitada para Trujillo en este instante. Te avisaremos en cuanto aparezca una).</i></p>
            <br>
            <a href="{URL_ONPE}" style="background-color: #0275d8; color: white; padding: 10px 18px; text-decoration: none; border-radius: 4px;">
                Ver Portal ONPE
            </a>
            </body></html>
            """
            enviar_correo(asunto, cuerpo)
        return

    # 2. Comparación contra el historial
    hay_cambios = False
    alertas_trujillo = []
    nuevas_generales = []

    for puesto, val in datos_actuales.items():
        # Ver si apareció Trujillo en este puesto
        if val["sedes_trujillo"]:
            sedes_antiguas = estado_previo.get(puesto, {}).get("sedes_trujillo", [])
            nuevas_sedes_trujillo = [s for s in val["sedes_trujillo"] if s not in sedes_antiguas]
            if nuevas_sedes_trujillo:
                alertas_trujillo.append((puesto, nuevas_sedes_trujillo))
                hay_cambios = True

        # Ver si es un puesto 100% nuevo
        if puesto not in estado_previo:
            nuevas_generales.append(puesto)
            hay_cambios = True

    # Despachar correos
    if alertas_trujillo:
        asunto = "🚨🚨 [URGENTE ONPE] ¡NUEVA PLAZA EN TRUJILLO / LA LIBERTAD! 🚨🚨"
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
