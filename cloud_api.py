"""
GesOrderPanel — Cloud API
=========================
Despliega en Railway.app (gratis).
El EA de MT5 consulta esta API en vez de un archivo local.

Endpoints:
  POST /api/order              ← Web app envía orden nueva
  GET  /api/orders/pending/raw ← MT5 EA obtiene órdenes en formato pipe
  POST /api/orders/ack         ← MT5 EA confirma orden ejecutada
  POST /api/orders/active      ← MT5 EA reporta sus órdenes activas
  GET  /api/orders/active      ← Web app lee órdenes activas en MT5
  GET  /api/status             ← Estado general
"""

from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import os
import uuid
from datetime import datetime

app = Flask(__name__)
CORS(app)

# ── Almacenamiento en memoria ─────────────────────────────────────────────
# (Railway mantiene el proceso activo entre requests)
pending_orders = []        # [{...order, target_instances:[id1,id2]}]
active_orders  = {}        # {instance_id: [...orders]}
open_positions = {}        # {instance_id: [...positions]}
instances      = {}        # {instance_id: {name, account, balance, last_seen}}
ea_last_seen   = None      # legacy compat
active_version = 0

# ── Helpers ───────────────────────────────────────────────────────────────
def now_str():
    return datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

def order_to_pipe(o: dict) -> str:
    """
    Convierte un orden a formato pipe que entiende el EA de MT5.
    ADD|SYMBOL|TYPE|ENTRY|SL|SL_MODE|TP|TP_MODE|LOTS|RISK_V|RISK_M|RR|COMMENT|ID
    """
    return "|".join([
        "ADD",
        str(o.get("symbol",    "EURUSD")).upper(),
        str(o.get("type",      "BUY_LIMIT")).upper(),
        str(o.get("entry",     0)),
        str(o.get("sl",        0)),
        str(o.get("sl_mode",   "PRICE")).upper(),
        str(o.get("tp",        0)),
        str(o.get("tp_mode",   "PRICE")).upper(),
        str(o.get("lots",      0.01)),
        str(o.get("risk_value",1)),
        str(o.get("risk_mode", "PCT")).upper(),
        str(o.get("rr",        2)),
        str(o.get("comment",   "GesOrder")).replace("|", "-"),
        str(o.get("id",        "")),
    ])


# ── RUTAS ─────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return jsonify({
        "app":     "GesOrderPanel Cloud API",
        "version": "1.0",
        "status":  "online",
        "ea_online": ea_last_seen is not None,
        "ea_last_seen": ea_last_seen,
        "pending": len([o for o in pending_orders if o["status"] == "pending"]),
        "active":  len(active_orders),
    })

@app.route("/api/status")
def api_status():
    return jsonify({
        "ok":        True,
        "time":      now_str(),
        "ea_online": ea_last_seen is not None,
        "ea_last_seen": ea_last_seen,
        "pending_count": len([o for o in pending_orders if o["status"] == "pending"]),
        "instances_count": len(instances),
    })



# ── INSTANCIAS MT5 ────────────────────────────────────────────────────────
@app.route("/api/instance/ping", methods=["POST"])
def instance_ping():
    """EA registra/actualiza su presencia."""
    global ea_last_seen
    data = request.get_json(force=True) or {}
    iid  = str(data.get("instance_id", "default"))
    instances[iid] = {
        "id":        iid,
        "name":      str(data.get("name",    "MT5")),
        "account":   str(data.get("account", "")),
        "balance":   float(data.get("balance", 0)),
        "last_seen": now_str(),
        "online":    True,
    }
    ea_last_seen = now_str()
    return jsonify({"ok": True})

@app.route("/api/instances", methods=["GET"])
def get_instances():
    """Lista instancias online, deduplicando por número de cuenta."""
    from datetime import datetime
    now_dt  = datetime.utcnow()
    seen    = {}   # account → best instance

    for inst in list(instances.values()):
        try:
            t      = datetime.strptime(inst["last_seen"], "%Y-%m-%d %H:%M:%S")
            age    = (now_dt - t).total_seconds()
            online = age < 35
        except:
            online = False
            age    = 9999

        if not online:
            continue  # ignorar offline

        acc = inst.get("account", inst["id"])
        # Quedarse con la instancia más reciente por cuenta
        if acc not in seen or inst["last_seen"] > seen[acc]["last_seen"]:
            seen[acc] = {**inst, "online": True}

    return jsonify(list(seen.values()))


# ── WEB APP → API ─────────────────────────────────────────────────────────
@app.route("/api/order", methods=["POST"])
def add_order():
    """Web app envía una nueva orden pendiente."""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"ok": False, "error": "Sin datos"}), 400

    symbol = str(data.get("symbol", "")).strip().upper()
    entry  = float(data.get("entry", 0))
    if not symbol:
        return jsonify({"ok": False, "error": "Símbolo requerido"}), 400
    if entry <= 0:
        return jsonify({"ok": False, "error": "Precio de entrada inválido"}), 400

    target = data.get("target_instances", [])
    if not isinstance(target, list): target = []

    order = {
        "id":               str(uuid.uuid4())[:8].upper(),
        "status":           "pending",
        "created_at":       now_str(),
        "target_instances": target,
        "executed_by":      [],
        "symbol":     symbol,
        "type":       str(data.get("type",       "BUY_LIMIT")).upper(),
        "entry":      entry,
        "sl":         float(data.get("sl",        0)),
        "sl_mode":    str(data.get("sl_mode",    "PRICE")).upper(),
        "tp":         float(data.get("tp",        0)),
        "tp_mode":    str(data.get("tp_mode",    "PRICE")).upper(),
        "lots":       float(data.get("lots",      0.01)),
        "risk_value": float(data.get("risk_value",1)),
        "risk_mode":  str(data.get("risk_mode",  "PCT")).upper(),
        "rr":         float(data.get("rr",        2)),
        "comment":    str(data.get("comment",    "GesOrder")),
    }

    pending_orders.append(order)
    print(f"[{now_str()}] ▸ Nueva orden: {order['type']} {order['symbol']} @ {order['entry']} ID:{order['id']}")
    return jsonify({"ok": True, "id": order["id"]})

# ── MT5 EA → API ──────────────────────────────────────────────────────────
@app.route("/api/orders/pending/raw")
def pending_raw():
    """
    MT5 EA hace GET aquí cada X segundos.
    Devuelve órdenes pendientes en formato pipe, una por línea.
    Texto plano para que el EA lo pueda parsear fácilmente.
    """
    global ea_last_seen
    ea_last_seen = now_str()

    instance_id = request.args.get("instance", "")
    orders = [
        o for o in pending_orders
        if o["status"] == "pending"
        and (not o.get("target_instances") or instance_id in o["target_instances"])
    ]
    if not orders:
        return Response("# no_orders\n", mimetype="text/plain")

    # Marcar como "processing" para evitar duplicados
    for o in orders:
        o["status"] = "processing"
        o["fetched_at"] = now_str()

    lines = [order_to_pipe(o) for o in orders]
    return Response("\n".join(lines) + "\n", mimetype="text/plain")

@app.route("/api/orders/ack", methods=["POST"])
def ack_order():
    """MT5 EA confirma que ejecutó una orden."""
    global ea_last_seen
    ea_last_seen = now_str()

    data      = request.get_json(force=True) or {}
    order_id  = str(data.get("id", ""))
    ticket    = data.get("ticket", "")
    success   = data.get("success", True)

    for o in pending_orders:
        if o["id"] == order_id:
            o["status"]     = "executed" if success else "failed"
            o["ticket"]     = ticket
            o["updated_at"] = now_str()
            print(f"[{now_str()}] {'✓' if success else '✗'} ACK orden {order_id} ticket:{ticket}")
            break

    return jsonify({"ok": True})

@app.route("/api/orders/recover", methods=["POST"])
def recover_stuck():
    """Devuelve a 'pending' órdenes en 'processing' por más de 15 segundos (por si el EA crasheó)."""
    from datetime import datetime, timedelta
    recovered = 0
    for o in pending_orders:
        if o["status"] == "processing":
            fetched = o.get("fetched_at", "")
            try:
                t = datetime.strptime(fetched, "%Y-%m-%d %H:%M:%S")
                if datetime.utcnow() - t > timedelta(seconds=15):
                    o["status"] = "pending"
                    recovered += 1
            except:
                o["status"] = "pending"
                recovered += 1
    return jsonify({"ok": True, "recovered": recovered})

@app.route("/api/orders/active", methods=["POST"])
def update_active():
    """MT5 EA reporta sus órdenes pendientes activas (reemplaza todo)."""
    global active_orders, ea_last_seen, active_version
    ea_last_seen = now_str()
    
    try:
        data = request.get_json(force=True, silent=True)
        if data is None:
            # Intentar limpiar el body manualmente
            raw = request.get_data(as_text=True)
            # Reemplazar comas decimales por puntos si el locale de MT5 usa coma
            import re
            raw_fixed = re.sub(r'(\d),(\d)', r'\1.\2', raw)
            import json as json_lib
            data = json_lib.loads(raw_fixed)
        active_orders = data if isinstance(data, list) else []
        active_version += 1
    except Exception as e:
        print(f"[{now_str()}] Error parseando active orders: {e} | raw: {request.get_data(as_text=True)[:200]}")
        return jsonify({"ok": False, "error": str(e)}), 400
    
    return jsonify({"ok": True, "count": len(active_orders), "version": active_version})

@app.route("/api/orders/active", methods=["GET"])
@app.route("/api/orders", methods=["GET"])
def get_active():
    """Devuelve órdenes activas con instance_id e instance_name."""
    try:
        instance_id = request.args.get("instance", "")
        # Formato viejo: lista plana — envolverla con info de instancia
        if isinstance(active_orders, list):
            fallback_name = next((i.get("name","?") for i in instances.values()), "MT5")
            return jsonify([{**o, "instance_id": "default", "instance_name": fallback_name}
                            for o in active_orders])
        # Filtro por instancia
        if instance_id:
            orders = active_orders.get(instance_id, [])
            name   = instances.get(instance_id, {}).get("name", instance_id)
            return jsonify([{**o, "instance_id": instance_id, "instance_name": name}
                            for o in orders])
        # Todas las instancias combinadas
        all_orders = []
        for iid, orders in active_orders.items():
            if not isinstance(orders, list):
                continue
            name = instances.get(iid, {}).get("name", iid)
            for o in orders:
                all_orders.append({**o, "instance_id": iid, "instance_name": name})
        return jsonify(all_orders)
    except Exception as e:
        print(f"[get_active] error: {e}")
        return jsonify([]), 200

@app.route("/api/orders/history", methods=["GET"])
def get_history():
    """Historial de órdenes procesadas."""
    done = [o for o in pending_orders if o["status"] in ("executed","failed")]
    return jsonify(done[-50:])  # Últimas 50

@app.route("/api/orders/cancel", methods=["POST"])
def cancel_pending():
    """Cancela una orden que todavía no fue ejecutada por el EA."""
    data = request.get_json(force=True) or {}
    order_id = str(data.get("id", ""))
    ticket   = str(data.get("ticket", ""))

    if order_id:
        for o in pending_orders:
            if o["id"] == order_id and o["status"] == "pending":
                o["status"] = "cancelled"
                return jsonify({"ok": True, "cancelled": "pending_order"})

    if ticket:
        # Cancelación de orden ya en MT5 — el EA la leerá en el próximo poll
        pending_orders.append({
            "id":         str(uuid.uuid4())[:8].upper(),
            "status":     "pending",
            "created_at": now_str(),
            "action":     "CANCEL",
            "ticket":     ticket,
            # Rellenos vacíos para compatibilidad pipe
            "symbol":"","type":"","entry":0,"sl":0,"sl_mode":"PRICE",
            "tp":0,"tp_mode":"PRICE","lots":0,"risk_value":0,
            "risk_mode":"LOTS","rr":0,"comment":"cancel",
        })
        return jsonify({"ok": True, "cancelled": "mt5_order"})

    return jsonify({"ok": False, "error": "id o ticket requerido"}), 400

# ── EA PING ───────────────────────────────────────────────────────────────
@app.route("/api/ping", methods=["GET", "POST"])
def ping():
    global ea_last_seen
    ea_last_seen = now_str()
    return jsonify({"ok": True, "time": ea_last_seen})


# ── POSICIONES ABIERTAS ───────────────────────────────────────────────────
@app.route("/api/positions", methods=["POST"])
def update_positions():
    """EA reporta posiciones abiertas — almacena por instancia."""
    global ea_last_seen
    ea_last_seen = now_str()
    instance_id  = request.args.get("instance", "default")
    try:
        data = request.get_json(force=True, silent=True)
        if data is None:
            raw = request.get_data(as_text=True)
            import re, json as json_lib
            data = json_lib.loads(re.sub(r'(\d),(\d)', r'\1.\2', raw))
        open_positions[instance_id] = data if isinstance(data, list) else []
        if instance_id in instances:
            instances[instance_id]["last_seen"] = now_str()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "count": len(open_positions.get(instance_id, []))})

@app.route("/api/positions", methods=["GET"])
def get_positions():
    """Devuelve posiciones. ?instance=ID para filtrar, o todas."""
    instance_id = request.args.get("instance", "")
    if instance_id:
        return jsonify(open_positions.get(instance_id, []))
    all_pos = []
    for iid, positions in open_positions.items():
        for p in positions:
            all_pos.append({**p, "instance_id": iid,
                            "instance_name": instances.get(iid, {}).get("name", iid)})
    return jsonify(all_pos)


# ── EXTRACCIÓN IA DESDE IMAGEN ────────────────────────────────────────────
@app.route("/api/extract-image", methods=["POST"])
def extract_image():
    """
    Proxy para llamar a la API de Anthropic desde el servidor.
    Requiere variable de entorno ANTHROPIC_API_KEY en Railway.
    """
    import requests as req

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({
            "ok": False,
            "error": "ANTHROPIC_API_KEY no configurada en Railway. Ve a tu proyecto → Variables → agrega ANTHROPIC_API_KEY"
        }), 500

    data       = request.get_json(force=True) or {}
    image_b64  = data.get("image")
    media_type = data.get("media_type", "image/png")

    if not image_b64:
        return jsonify({"ok": False, "error": "Sin imagen"}), 400

    prompt = """Analiza esta imagen de trading y extrae exactamente estos 3 valores numéricos:

1. PRECIO DE ENTRADA: busca "Precio de entrada", "Entry", "Entrada", "Open", "Price"
2. STOP LOSS: busca sección "NIVEL DE STOP" o "Stop Loss" o "SL" — usa el valor de "Precio" (NO Ticks)
3. TAKE PROFIT: busca sección "NIVEL DE BENEFICIO" o "Take Profit" o "TP" — usa el valor de "Precio" (NO Ticks)

Si hay "Ticks" y "Precio" en la misma sección, usa siempre "Precio".

Responde ÚNICAMENTE con JSON, sin texto adicional:
{"entry": 211.514, "sl": 211.213, "tp": 211.716}

Si no encuentras algún valor usa null. Solo números."""

    try:
        resp = req.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      "claude-3-5-sonnet-20241022",
                "max_tokens": 300,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                        {"type": "text",  "text": prompt}
                    ]
                }]
            },
            timeout=20
        )
        result = resp.json()
        print(f"[extract] HTTP {resp.status_code} keys:{list(result.keys())}")

        # Detectar error de Anthropic
        if result.get("type") == "error" or "error" in result:
            err = result.get("error", {})
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return jsonify({"ok": False, "error": f"Anthropic: {msg}"}), 500

        text = ""
        for block in result.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")

        print(f"[extract] Texto: '{text[:300]}'")
        if not text.strip():
            return jsonify({"ok": False, "error": f"Respuesta vacía: {str(result)[:200]}"}), 500

        import re, json as json_lib

        # Intentar parsear JSON directo
        try:
            values = json_lib.loads(text.strip())
        except:
            # Buscar JSON dentro del texto con regex más flexible
            match = re.search(r'\{.*?\}', text, re.DOTALL)
            if not match:
                # Extraer números manualmente como fallback
                nums = re.findall(r'[\d]+\.[\d]+', text)
                print(f"[extract] Fallback nums: {nums}")
                if len(nums) >= 1:
                    values = {
                        "entry": float(nums[0]) if len(nums) > 0 else None,
                        "sl":    float(nums[2]) if len(nums) > 2 else None,
                        "tp":    float(nums[1]) if len(nums) > 1 else None,
                    }
                else:
                    return jsonify({"ok": False, "error": f"No se pudo parsear: {text[:150]}"}), 500
            else:
                try:
                    values = json_lib.loads(match.group())
                except Exception as je:
                    return jsonify({"ok": False, "error": f"JSON inválido: {match.group()[:100]}"}), 500

        return jsonify({"ok": True, "values": values})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── INICIO ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"GesOrderPanel Cloud API — puerto {port}")
    app.run(host="0.0.0.0", port=port)

