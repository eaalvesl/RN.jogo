# app.py (versão corrigida)

import os
import json
import sqlite3
import hashlib
import secrets
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, g, make_response
from flask_cors import CORS

DATABASE = 'borboletario.db'
DEBUG = True

app = Flask(__name__)
app.config.from_object(__name__)
CORS(app, supports_credentials=True)

STAGES = ['ovo', 'larva', 'casulo', 'adulto']

# --- Banco e helpers ---
def init_db():
    if os.path.exists(DATABASE):
        return
    with sqlite3.connect(DATABASE) as conn:
        conn.executescript("""
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE butterflies (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            base_species TEXT,
            generation INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            stage TEXT NOT NULL,
            traits TEXT NOT NULL,  -- SQLite não tem JSON, armazenamos como TEXT
            rare INTEGER NOT NULL DEFAULT 0,
            parents TEXT,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
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
        try:
            d['traits'] = json.loads(d['traits'])
        except json.JSONDecodeError:
            d['traits'] = {}
    # Converter parents string para lista
    if 'parents' in d and d['parents'] and isinstance(d['parents'], str):
        try:
            d['parents'] = [p.strip() for p in d['parents'].split(',') if p.strip()]
        except:
            d['parents'] = []
    return d

def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    if one: return row_to_dict(rv[0]) if rv else None
    return [row_to_dict(r) for r in rv]

def insert_butterfly(butterfly):
    db = get_db()
    parents_str = None
    parents_list = butterfly.get('parents')
    if parents_list and isinstance(parents_list, list):
        parents_str = ','.join(str(p) for p in parents_list)
    db.execute('''
        INSERT INTO butterflies (id, user_id, base_species, generation, created_at, stage, traits, rare, parents, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        butterfly['id'],
        butterfly['user_id'],
        butterfly.get('base_species'),
        butterfly.get('generation', 1),
        butterfly.get('created_at', datetime.utcnow().isoformat()),
        butterfly.get('stage', 'ovo'),
        json.dumps(butterfly['traits']),
        int(butterfly.get('rare', 0)),
        parents_str,
        datetime.utcnow().isoformat()
    ))
    db.commit()

# --- Hash de senha ---
def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100_000)
    return dk.hex(), salt

def verify_password(password, hash_hex, salt):
    test_hex, _ = hash_password(password, salt)
    return secrets.compare_digest(test_hex, hash_hex)

# --- Autenticação ---
@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    if not username or not password:
        return jsonify({'error':'Campos obrigatórios'}), 400
    username = username.strip().lower()
    if len(username)<3 or len(username)>24:
        return jsonify({'error':'Usuário deve ter 3–24 caracteres'}), 400
    if len(password)<6:
        return jsonify({'error':'Senha muito curta'}), 400

    db = get_db()
    user = query_db('SELECT * FROM users WHERE username = ?', (username,), one=True)
    if user:
        return jsonify({'error':'Usuário já existe'}), 409

    user_id = 'u-'+secrets.token_hex(10)
    hash_hex, salt = hash_password(password)
    db.execute('INSERT INTO users (id, username, password_hash, salt) VALUES (?, ?, ?, ?)',
               (user_id, username, hash_hex, salt))
    db.commit()
    return jsonify({'success':True, 'user_id':user_id, 'username':username})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    if not username or not password:
        return jsonify({'error':'Campos obrigatórios'}), 400
    username = username.strip().lower()

    db = get_db()
    user = query_db('SELECT * FROM users WHERE username = ?', (username,), one=True)
    if not user:
        # Hash dummy para evitar timing attack (opcional, mas recomendado)
        hashlib.pbkdf2_hmac('sha256', b'dummy', b'dummy', 100_000)
        return jsonify({'error':'Usuário ou senha inválidos'}), 401

    if not verify_password(password, user['password_hash'], user['salt']):
        return jsonify({'error':'Usuário ou senha inválidos'}), 401

    # Aqui você poderia gerar um JWT real. Por simplicidade, retorna user_id.
    return jsonify({'success':True, 'user_id':user['id'], 'username':user['username']})

# --- Middleware de autenticação ---
def require_auth(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = request.headers.get('X-User-Id') or request.cookies.get('user_id')
        if not user_id:
            # Resposta com header WWW-Authenticate pode ajudar clientes
            return jsonify({'error':'Unauthorized'}), 401
        user = query_db('SELECT id FROM users WHERE id = ?', (user_id,), one=True)
        if not user:
            return jsonify({'error':'Usuário não existe'}), 401
        g.user_id = user_id
        return f(*args, **kwargs)
    return decorated

# --- Rotas protegidas ---
@app.route('/api/butterflies', methods=['GET'])
@require_auth
def get_gallery():
    butterflies = query_db('''
        SELECT * FROM butterflies
        WHERE user_id = ? AND stage = 'adulto'
        ORDER BY created_at DESC
        LIMIT 200
    ''', (g.user_id,))
    return jsonify(butterflies)

@app.route('/api/butterflies', methods=['POST'])
@require_auth
def create_butterfly():
    data = request.get_json()
    required = ['id', 'traits', 'stage']
    for r in required:
        if r not in data:
            return jsonify({'error': f'Missing {r}'}), 400

    parents = data.get('parents')
    if parents and not isinstance(parents, list):
        # Aceita parents como string (compatibilidade)
        try:
            parents = [p.strip() for p in str(parents).split(',') if p.strip()]
        except:
            parents = []

    base_species = data.get('base_species')
    if parents and not base_species:
        base_species = 'híbrida'

    butterfly = {
        'id': data['id'],
        'user_id': g.user_id,
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
    except sqlite3.IntegrityError as e:
        return jsonify({'error': f'ID already exists: {str(e)}'}), 409

@app.route('/api/butterflies/<butterfly_id>/advance', methods=['POST'])
@require_auth
def advance_stage(butterfly_id):
    b = query_db('SELECT * FROM butterflies WHERE id = ? AND user_id = ?', (butterfly_id, g.user_id), one=True)
    if not b:
        return jsonify({'error': 'Not found'}), 404
    try:
        curr_idx = STAGES.index(b['stage'])
    except ValueError:
        return jsonify({'error': 'Estágio inválido'}), 400
    if curr_idx >= len(STAGES)-1:
        return jsonify({'error': 'Already adult'}), 400
    next_stage = STAGES[curr_idx + 1]
    db = get_db()
    db.execute('UPDATE butterflies SET stage=?, updated_at=? WHERE id=? AND user_id=?',
               (next_stage, datetime.utcnow().isoformat(), butterfly_id, g.user_id))
    db.commit()
    updated = query_db('SELECT * FROM butterflies WHERE id = ? AND user_id = ?', (butterfly_id, g.user_id), one=True)
    return jsonify(updated)

@app.route('/api/butterflies/<butterfly_id>/traits', methods=['POST'])
@require_auth
def update_traits(butterfly_id):
    data = request.get_json()
    if not data or 'traits' not in data:
        return jsonify({'error': 'Missing traits'}), 400
    db = get_db()
    db.execute('UPDATE butterflies SET traits=?, updated_at=? WHERE id=? AND user_id=?',
               (json.dumps(data['traits']), datetime.utcnow().isoformat(), butterfly_id, g.user_id))
    db.commit()
    b = query_db('SELECT * FROM butterflies WHERE id = ? AND user_id = ?', (butterfly_id, g.user_id), one=True)
    return jsonify(b)

@app.route('/api/ranking', methods=['GET'])
def ranking():
    # Agrupa por base_species, pattern, size (quando disponíveis) para evitar agrupar por JSON inteiro
    r = query_db('''
        SELECT
            traits,
            COUNT(*) as count,
            MIN(created_at) as first_created
        FROM butterflies
        WHERE stage='adulto'
        GROUP BY traits
        ORDER BY count ASC, first_created ASC
        LIMIT 50
    ''')

    def safe_get_traits_key(t):
        if not isinstance(t, dict):
            return 'Desconhecido'
        base = (t.get('base_species') or t.get('species') or 'Híbrida')
        pat = t.get('pattern', '')
        sz = t.get('size', '')
        color = t.get('color', '')
        # Combina os atributos relevantes para formar uma chave legível
        parts = [base]
        if pat: parts.append(pat)
        if color: parts.append(color)
        if sz: parts.append(sz)
        return ' '.join(parts).strip()

    seen, enriched = set(), []
    for b in r:
        traits_obj = b['traits'] if isinstance(b['traits'], dict) else {}
        k = safe_get_traits_key(traits_obj)
        if k in seen: continue
        seen.add(k)
        enriched.append({
            'name': k,
            'count': b['count'],
            'sample_traits': traits_obj
        })
    # Ordena e limita para exibir os 20 mais raros
    enriched = sorted(enriched, key=lambda x: x['count'])[:20]
    return jsonify(enriched)

# --- Inicialização ---
@app.before_first_request
def setup():
    init_db()

if __name__ == '__main__':
    app.run(port=5000, debug=True)
