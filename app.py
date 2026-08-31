# app.py
import os
import json
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, g
from flask_cors import CORS

DATABASE = 'borboletario.db'
DEBUG = True

app = Flask(__name__)
app.config.from_object(__name__)
CORS(app)

SPECIES = {
    'monarca': {'name': 'Monarca', 'color1': '#ffb74d', 'color2': '#ffd54f', 'pattern': 'stripes', 'size': 'médio'},
    'azulada': {'name': 'Azul‑céu', 'color1': '#4fc3f7', 'color2': '#a5d6a7', 'pattern': 'spots', 'size': 'pequeno'},
    'nocturna': {'name': 'Nocturna', 'color1': '#7e57c2', 'color2': '#b39ddb', 'pattern': 'plain', 'size': 'médio'},
}
STAGES = ['ovo', 'larva', 'casulo', 'adulto']

# --- Criação do banco inline ---
def init_db():
    if os.path.exists(DATABASE):
        return  # já existe
    with sqlite3.connect(DATABASE) as conn:
        conn.executescript("""
        CREATE TABLE butterflies (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'demo',
            base_species TEXT,
            generation INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            stage TEXT NOT NULL,
            traits JSON NOT NULL,
            rare INTEGER NOT NULL DEFAULT 0,
            parents TEXT
        );
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            name TEXT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            stats JSON DEFAULT '{}'
        );
        INSERT OR IGNORE INTO users (id, name) VALUES ('demo', 'Jogador');
        """)

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def row_to_dict(row):
    if not row: return None
    d = dict(row)
    if 'traits' in d and isinstance(d['traits'], str):
        d['traits'] = json.loads(d['traits'])
    return d

def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    if one: return row_to_dict(rv[0]) if rv else None
    return [row_to_dict(r) for r in rv]

def insert_butterfly(butterfly):
    db = get_db()
    db.execute('''
        INSERT INTO butterflies (id, user_id, base_species, generation, created_at, stage, traits, rare, parents)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        butterfly['id'],
        butterfly.get('user_id', 'demo'),
        butterfly.get('base_species'),
        butterfly.get('generation', 1),
        butterfly.get('created_at', datetime.utcnow().isoformat()),
        butterfly.get('stage', 'ovo'),
        json.dumps(butterfly['traits']),
        int(butterfly.get('rare', 0)),
        ','.join(butterfly['parents']) if butterfly.get('parents') else None
    ))
    db.commit()

# --- Rotas ---
@app.route('/api/butterflies', methods=['GET'])
def get_gallery():
    user_id = request.args.get('user_id', 'demo')
    butterflies = query_db('''
        SELECT * FROM butterflies
        WHERE user_id = ? AND stage = 'adulto'
        ORDER BY created_at DESC
        LIMIT 100
    ''', (user_id,))
    return jsonify(butterflies)

@app.route('/api/butterflies', methods=['POST'])
def create_butterfly():
    data = request.get_json()
    required = ['id', 'traits', 'stage']
    for r in required:
        if r not in data:
            return jsonify({'error': f'Missing {r}'}), 400

    parents = data.get('parents')
    base_species = data.get('base_species')
    if parents and not base_species:
        base_species = 'híbrida'

    butterfly = {
        'id': data['id'],
        'user_id': data.get('user_id', 'demo'),
        'base_species': base_species,
        'generation': data.get('generation', 1),
        'created_at': data.get('created_at', datetime.utcnow().isoformat()),
        'stage': data['stage'],
        'traits': data['traits'],
        'rare': data.get('rare', 0),
        'parents': parents
    }
    try:
        insert_butterfly(butterfly)
        return jsonify({'success': True, 'butterfly': butterfly}), 201
    except sqlite3.IntegrityError:
        return jsonify({'error': 'ID already exists'}), 409

@app.route('/api/butterflies/<butterfly_id>/advance', methods=['POST'])
def advance_stage(butterfly_id):
    b = query_db('SELECT * FROM butterflies WHERE id = ?', (butterfly_id,), one=True)
    if not b:
        return jsonify({'error': 'Not found'}), 404

    curr_idx = STAGES.index(b['stage'])
    if curr_idx >= len(STAGES)-1:
        return jsonify({'error': 'Already adult'}), 400

    next_stage = STAGES[curr_idx + 1]
    db = get_db()
    db.execute('UPDATE butterflies SET stage=? WHERE id=?', (next_stage, butterfly_id))
    db.commit()
    updated = query_db('SELECT * FROM butterflies WHERE id = ?', (butterfly_id,), one=True)
    return jsonify(updated)

# --- Inicializar banco na primeira execução ---
@app.before_first_request
def setup():
    init_db()

if __name__ == '__main__':
    app.run(port=5000, debug=True)
