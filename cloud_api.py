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
pending_orders = []   # Órdenes pendientes de ejecución por MT5
active_orders  = []   # Órdenes activas reportadas por MT5
ea_last_seen   = None # Última vez que el EA hizo ping

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
        "active_count":  len(active_orders),
    })

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

    order = {
        "id":         str(uuid.uuid4())[:8].upper(),
        "status":     "pending",
        "created_at": now_str(),
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

    orders = [o for o in pending_orders if o["status"] == "pending"]
    if not orders:
        return Response("# no_orders\n", mimetype="text/plain")

    # Marcar inmediatamente como "processing" para evitar duplicados
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
    global active_orders, ea_last_seen
    ea_last_seen  = now_str()
    active_orders = request.get_json(force=True) or []
    return jsonify({"ok": True, "count": len(active_orders)})

@app.route("/api/orders/active", methods=["GET"])
@app.route("/api/orders", methods=["GET"])  # alias para compatibilidad web panel
def get_active():
    """Web app lee las órdenes activas en MT5."""
    return jsonify(active_orders)

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


# ── INICIO ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"GesOrderPanel Cloud API — puerto {port}")
    app.run(host="0.0.0.0", port=port)
