"""
GesOrderPanel Bridge Server
===========================
Servidor local que conecta el panel web con MT5.
Lee/escribe en los archivos de la carpeta Common de MT5.

Instalación:
  pip install flask flask-cors

Uso:
  python bridge.py

Luego abre: http://localhost:5000
"""

from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
import os
import json
import platform
from pathlib import Path
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder='.')
CORS(app)

def get_mt5_common_path():
    """Detecta la ruta de archivos comunes de MT5 según el sistema."""
    if platform.system() == 'Windows':
        appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
        candidates = [
            os.path.join(appdata, 'MetaQuotes', 'Terminal', 'Common', 'Files'),
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        # Si no existe, crear el default
        default = os.path.join(appdata, 'MetaQuotes', 'Terminal', 'Common', 'Files')
        os.makedirs(default, exist_ok=True)
        return default
    else:
        # Linux/Mac - usar directorio local como fallback
        path = os.path.join(os.path.expanduser('~'), 'MT5_Files')
        os.makedirs(path, exist_ok=True)
        return path

MT5_PATH     = get_mt5_common_path()
BRIDGE_FILE  = os.path.join(MT5_PATH, 'gesorder_bridge.txt')
STATUS_FILE  = os.path.join(MT5_PATH, 'gesorder_status.txt')

print(f"📁 MT5 Common Files: {MT5_PATH}")
print(f"📄 Bridge file:      {BRIDGE_FILE}")
print(f"📊 Status file:      {STATUS_FILE}")


# ── Helpers ──────────────────────────────────────────────────────────────────
def ensure_bridge_file():
    if not os.path.exists(BRIDGE_FILE):
        with open(BRIDGE_FILE, 'w', encoding='utf-8') as f:
            f.write('# GesOrderPanel Bridge File\n')

def build_order_line(data: dict) -> str:
    """
    Formato del archivo bridge:
    ADD|SYMBOL|TYPE|ENTRY|SL|SL_MODE|TP|TP_MODE|LOTS|RISK_VALUE|RISK_MODE|RR|COMMENT
    """
    parts = [
        'ADD',
        str(data.get('symbol', 'EURUSD')).upper().strip(),
        str(data.get('type', 'BUY_LIMIT')).upper().strip(),
        str(data.get('entry',      0)),
        str(data.get('sl',         0)),
        str(data.get('sl_mode',    'PRICE')).upper().strip(),    # PRICE | POINTS
        str(data.get('tp',         0)),
        str(data.get('tp_mode',    'PRICE')).upper().strip(),    # PRICE | POINTS | RR
        str(data.get('lots',       0.01)),
        str(data.get('risk_value', 1.0)),
        str(data.get('risk_mode',  'PCT')).upper().strip(),      # LOTS | PCT | USD
        str(data.get('rr',         2.0)),
        str(data.get('comment',    'GesOrder')).replace('|', '-'),
    ]
    return '|'.join(parts)


# ── Rutas API ────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    """Servir el panel web."""
    return send_from_directory('.', 'index.html')

@app.route('/api/status', methods=['GET'])
def api_status():
    """Estado del bridge."""
    return jsonify({
        'ok': True,
        'mt5_path': MT5_PATH,
        'bridge_file': BRIDGE_FILE,
        'bridge_exists': os.path.exists(BRIDGE_FILE),
        'status_file': STATUS_FILE,
        'status_exists': os.path.exists(STATUS_FILE),
        'time': datetime.now().isoformat(),
    })

@app.route('/api/order', methods=['POST'])
def api_add_order():
    """Agregar una orden pendiente."""
    data = request.get_json(force=True)
    if not data:
        return jsonify({'ok': False, 'error': 'No JSON body'}), 400

    # Validación mínima
    symbol = str(data.get('symbol', '')).strip()
    if not symbol:
        return jsonify({'ok': False, 'error': 'Símbolo requerido'}), 400

    entry = float(data.get('entry', 0))
    if entry <= 0:
        return jsonify({'ok': False, 'error': 'Precio de entrada inválido'}), 400

    line = build_order_line(data)

    ensure_bridge_file()
    with open(BRIDGE_FILE, 'a', encoding='utf-8') as f:
        f.write(line + '\n')

    print(f"[{datetime.now().strftime('%H:%M:%S')}] ▸ Orden escrita: {line}")
    return jsonify({'ok': True, 'line': line})

@app.route('/api/cancel', methods=['POST'])
def api_cancel_order():
    """Cancelar orden pendiente por ticket."""
    data = request.get_json(force=True)
    ticket = data.get('ticket')
    if not ticket:
        return jsonify({'ok': False, 'error': 'Ticket requerido'}), 400

    line = f"CANCEL|{ticket}|||||||||||||"

    ensure_bridge_file()
    with open(BRIDGE_FILE, 'a', encoding='utf-8') as f:
        f.write(line + '\n')

    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✗ Cancelar ticket: {ticket}")
    return jsonify({'ok': True, 'ticket': ticket})

@app.route('/api/orders', methods=['GET'])
def api_get_orders():
    """Leer órdenes pendientes activas desde el archivo de estado."""
    try:
        if not os.path.exists(STATUS_FILE):
            return jsonify([])

        with open(STATUS_FILE, 'r', encoding='utf-8') as f:
            lines = f.read().strip().split('\n')

        orders = []
        for line in lines[1:]:  # Saltar encabezado
            parts = line.split('|')
            if len(parts) >= 9:
                orders.append({
                    'ticket':  parts[0].strip(),
                    'symbol':  parts[1].strip(),
                    'type':    parts[2].strip(),
                    'entry':   parts[3].strip(),
                    'sl':      parts[4].strip(),
                    'tp':      parts[5].strip(),
                    'lots':    parts[6].strip(),
                    'comment': parts[7].strip(),
                    'time':    parts[8].strip(),
                })
        return jsonify(orders)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Inicio ───────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    ensure_bridge_file()
    print()
    print("=" * 55)
    print("  🚀 GesOrderPanel Bridge Server")
    print("=" * 55)
    print(f"  🌐 Panel web:    http://localhost:5000")
    print(f"  📁 MT5 Files:    {MT5_PATH}")
    print(f"  📄 Bridge:       gesorder_bridge.txt")
    print(f"  📊 Status:       gesorder_status.txt")
    print("=" * 55)
    print()
    app.run(host='0.0.0.0', port=5000, debug=False)
