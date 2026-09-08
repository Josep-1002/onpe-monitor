import os
import json
import re
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from playwright.sync_api import sync_playwright

URL_ONPE = "https://reclutamiento.onpe.gob.pe/convocatorias"
STATE_FILE = "estado_previo.json"

# Zonas prioritarias (Trujillo y distritos de La Libertad)
ZONAS_TRUJILLO = [
    "TRUJILLO",
    "LA ESPERANZA",
    "VICTOR LARCO HERRERA",
    "PACASMAYO"
]

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASS = os.getenv("GMAIL_APP_PASS")
EMAIL_DESTINO = os.getenv("EMAIL_DESTINO")


def enviar_correo(asunto, html_cuerpo):
    if not GMAIL_USER or not GMAIL_APP_PASS or not EMAIL_DESTINO:
        print("[!] Faltan credenciales de Gmail en las variables de entorno.")
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
        print(f"[+] Correo enviado exitosamente a {EMAIL_DESTINO}")
    except Exception as e:
        print(f"[-] Error al enviar el correo: {e}")


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
    print(f"[+] Historial actualizado y guardado en {STATE_FILE}")


def extraer_convocatorias():
    datos = {}
    with sync_playwright() as p:
        # Lanzamiento con parámetros de sigilo para evitar detección por WAF
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--window-size=1920,1080"
            ]
        )

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="es-PE",
            timezone_id="America/Lima",
            extra_http_headers={
                "Accept-Language": "es-PE,es-419;q=0.9,es;q=0.8,en;q=0.7",
                "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Upgrade-Insecure-Requests": "1"
            }
        )

        page = context.new_page()

        # Inyección de script para ocultar huellas de Playwright
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['es-PE', 'es', 'en']
            });
        """)

        print(f"[*] Conectando a {URL_ONPE}...")
        try:
            page.goto(URL_ONPE, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)
        except Exception as e:
            print(f"[i] Aviso de conexión inicial: {e}")

        # Verificación de bloqueo WAF
        contenido_inicial = page.locator("body").inner_text()
        if "bloqueado" in contenido_inicial.lower() or "captcha_box" in contenido_inicial.lower():
            print("[!] El cortafuegos de la ONPE presentó un desafío temporal. Se reintentará en el próximo ciclo.")
            browser.close()
            return datos

        # Esperamos que Angular dibuje las tarjetas
        try:
            page.wait_for_selector(".card-normal", timeout=25000)
        except Exception:
            print("[!] No se detectó '.card-normal'. Posible demora de red.")
            browser.close()
            return datos

        total_ofertas = page.locator(".card-normal").count()
        print(f"[*] Ofertas detectadas: {total_ofertas}")

        if total_ofertas == 0:
            browser.close()
            return datos

        for i in range(total_ofertas):
            try:
                # Asegurar que estamos en la lista de tarjetas
                page.wait_for_selector(".card-normal", timeout=10000)
                tarjeta = page.locator(".card-normal").nth(i)

                titulo = tarjeta.locator("h2").first.inner_text().strip()
                if not titulo:
                    titulo = f"Puesto #{i+1}"

                print(f"[{i+1}/{total_ofertas}] Leyendo sedes de: {titulo}...")

                # Clic en Ver Detalle
                btn_detalle = tarjeta.locator("button.button-none, button:has-text('Ver Detalle')")
                if btn_detalle.count() > 0:
                    btn_detalle.first.click()
                else:
                    tarjeta.click()

                # Espera natural a que cargue la lista de sedes
                page.wait_for_timeout(2000)

                # Extraer las sedes
                items_li = page.locator("li").all_inner_texts()
                sedes_validas = [li.strip() for li in items_li if "Cantidad requerida:" in li or "Plazo para postulación:" in li]

                # Búsqueda de Trujillo y sus distritos
                zonas_detectadas = []
                for s in sedes_validas:
                    for z in ZONAS_TRUJILLO:
                        if re.search(rf"\b{re.escape(z)}\b", s.upper()):
                            zonas_detectadas.append(s)
                            break

                datos[titulo] = {
                    "total_sedes": len(sedes_validas),
                    "sedes": sedes_validas,
                    "sedes_trujillo": zonas_detectadas
                }

                # Volver atrás usando el botón de la interfaz o el historial del navegador
                btn_volver = page.locator("button:has-text('Volver'), button:has-text('Regresar'), a:has-text('Volver'), a:has-text('Regresar')")
                if btn_volver.count() > 0 and btn_volver.first.is_visible():
                    btn_volver.first.click()
                else:
                    page.go_back()

                # Pausa humana antes de la siguiente tarjeta
                page.wait_for_timeout(1500)

            except Exception as err:
                print(f"[-] Omitiendo oferta {i}: {err}")
                # Si falló la navegación atrás, hacemos un retroceso seguro
                try:
                    page.go_back()
                    page.wait_for_timeout(2000)
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

    # 1. Si es la primera ejecución histórica
    if not estado_previo:
        print("[+] Guardando estado base inicial...")
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
            <h2 style="color: #d9534f;">¡Atención! Hay plazas activas en Trujillo / La Libertad:</h2>
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
            <p>Se están vigilando <b>{len(datos_actuales)} ofertas laborales</b> cada 30 minutos:</p>
            <ul>{resumen_puestos}</ul>
            <p><i>(Ninguna tiene sede habilitada para Trujillo en este momento. Te avisaremos apenas aparezca una).</i></p>
            <br>
            <a href="{URL_ONPE}" style="background-color: #0275d8; color: white; padding: 10px 18px; text-decoration: none; border-radius: 4px;">
                Ver Portal ONPE
            </a>
            </body></html>
            """
            enviar_correo(asunto, cuerpo)
        return

    # 2. Detección de cambios respecto a la última ejecución
    hay_cambios = False
    alertas_trujillo = []
    nuevas_generales = []

    for puesto, val in datos_actuales.items():
        # Ver si se habilitó Trujillo
        if val["sedes_trujillo"]:
            sedes_antiguas = estado_previo.get(puesto, {}).get("sedes_trujillo", [])
            nuevas_sedes_trujillo = [s for s in val["sedes_trujillo"] if s not in sedes_antiguas]
            if nuevas_sedes_trujillo:
                alertas_trujillo.append((puesto, nuevas_sedes_trujillo))
                hay_cambios = True

        # Ver si es un puesto nuevo
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
            <h3>Se publicaron nuevas ofertas en la ONPE:</h3>
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
