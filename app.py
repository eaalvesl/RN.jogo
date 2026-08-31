# app.py
import os
import json
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, g, send_from_directory
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
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            stats JSON DEFAULT '{}'
        );
        CREATE TABLE butterflies (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            base_species TEXT,
            generation INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            stage TEXT NOT NULL,
            traits JSON NOT NULL,
            rare INTEGER NOT NULL DEFAULT 0,
            parents TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            key TEXT NOT NULL,
            unlocked_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, key)
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
        d['traits'] = json.loads(d['traits'])
    if 'stats' in d and isinstance(d['stats'], str):
        d['stats'] = json.loads(d['stats'])
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
        butterfly['user_id'],
        butterfly.get('base_species'),
        butterfly.get('generation', 1),
        butterfly.get('created_at', datetime.utcnow().isoformat()),
        butterfly.get('stage', 'ovo'),
        json.dumps(butterfly['traits']),
        int(butterfly.get('rare', 0)),
        ','.join(butterfly['parents']) if butterfly.get('parents') else None
    ))
    db.commit()

# --- Hash e autenticação (mesmo do anterior) ---
def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100_000)
    return dk.hex(), salt

def verify_password(password, hash_hex, salt):
    test_hex, _ = hash_password(password, salt)
    return secrets.compare_digest(test_hex, hash_hex)

@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username'); password = data.get('password')
    if not username or not password: return jsonify({'error':'Campos obrigatórios'}), 400
    if len(username)<3 or len(username)>24: return jsonify({'error':'Usuário 3–24 caracteres'}), 400
    if len(password)<6: return jsonify({'error':'Senha muito curta'}), 400
    user = query_db('SELECT * FROM users WHERE username = ?', (username.lower(),), one=True)
    if user: return jsonify({'error':'Usuário já existe'}), 409
    user_id = 'u-'+secrets.token_hex(10)
    hash_hex, salt = hash_password(password)
    db = get_db()
    db.execute('INSERT INTO users (id, username, password_hash, salt) VALUES (?, ?, ?, ?)',
               (user_id, username.lower(), hash_hex, salt))
    db.commit()
    return jsonify({'success':True, 'user_id':user_id, 'username':username.lower()})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username'); password = data.get('password')
    if not username or not password: return jsonify({'error':'Campos obrigatórios'}), 400
    user = query_db('SELECT * FROM users WHERE username = ?', (username.lower(),), one=True)
    if not user: return jsonify({'error':'Usuário ou senha inválidos'}), 401
    if not verify_password(password, user['password_hash'], user['salt']):
        return jsonify({'error':'Usuário ou senha inválidos'}), 401
    return jsonify({'success':True, 'user_id':user['id'], 'username':user['username']})

def require_auth(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = request.headers.get('X-User-Id') or request.cookies.get('user_id')
        if not user_id: return jsonify({'error':'Unauthorized'}), 401
        user = query_db('SELECT id FROM users WHERE id = ?', (user_id,), one=True)
        if not user: return jsonify({'error':'Usuário não existe'}), 401
        g.user_id = user_id
        return f(*args, **kwargs)
    return decorated

# --- Rotas de borboletas (protegidas) ---
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
        if r not in data: return jsonify({'error': f'Missing {r}'}), 400
    parents = data.get('parents')
    base_species = data.get('base_species')
    if parents and not base_species: base_species = 'híbrida'
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
        # Conquista: primeira criação
        unlock_achievement_simple(g.user_id, 'primeira_criacao', 'Primeira borboleta criada')
        # Conquista: mutante
        if butterfly.get('rare'): unlock_achievement_simple(g.user_id, 'mutante', 'Mutante rara')
        return jsonify({'success': True, 'butterfly': butterfly}), 201
    except sqlite3.IntegrityError:
        return jsonify({'error': 'ID already exists'}), 409

@app.route('/api/butterflies/<butterfly_id>/advance', methods=['POST'])
@require_auth
def advance_stage(butterfly_id):
    b = query_db('SELECT * FROM butterflies WHERE id = ? AND user_id = ?', (butterfly_id, g.user_id), one=True)
    if not b: return jsonify({'error': 'Not found'}), 404
    curr_idx = STAGES.index(b['stage'])
    if curr_idx >= len(STAGES)-1: return jsonify({'error': 'Already adult'}), 400
    next_stage = STAGES[curr_idx + 1]
    db = get_db()
    db.execute('UPDATE butterflies SET stage=? WHERE id=? AND user_id=?', (next_stage, butterfly_id, g.user_id))
    db.commit()
    updated = query_db('SELECT * FROM butterflies WHERE id = ? AND user_id = ?', (butterfly_id, g.user_id), one=True)
    # Conquista: chegou à fase adulta
    if next_stage=='adulto':
        unlock_achievement_simple(g.user_id, 'adulto', 'Primeira fase adulta')
    return jsonify(updated)

@app.route('/api/butterflies/<butterfly_id>/traits', methods=['POST'])
@require_auth
def update_traits(butterfly_id):
    data = request.get_json()
    if not data or 'traits' not in data: return jsonify({'error': 'Missing traits'}), 400
    db = get_db()
    db.execute('UPDATE butterflies SET traits=? WHERE id=? AND user_id=?', (json.dumps(data['traits']), butterfly_id, g.user_id))
    db.commit()
    b = query_db('SELECT * FROM butterflies WHERE id = ? AND user_id = ?', (butterfly_id, g.user_id), one=True)
    return jsonify(b)

# --- RANKING (mesmo) ---
@app.route('/api/ranking', methods=['GET'])
def ranking():
    r = query_db('''
        SELECT traits, COUNT(*) as count, MIN(created_at) as first_created
        FROM butterflies WHERE stage='adulto'
        GROUP BY traits ORDER BY count ASC, first_created ASC LIMIT 20
    ''')
    def key(b): t=b['traits']; return f"{t.get('base_species','Híbrida')} {t.get('pattern','')} {t.get('size','')}".strip()
    seen, enriched = set(), []
    for b in r:
        k = key(b)
        if k in seen: continue
        seen.add(k)
        enriched.append({'name':k,'count':b['count'],'sample_traits':b['traits']})
    return jsonify(enriched)

# --- ===== NOVO: COMPARTILHAMENTO ===== ---
@app.route('/api/share/<butterfly_id>', methods=['GET'])
def share_butterfly(butterfly_id):
    """Retorna borboleta + info do dono, público."""
    b = query_db('''
        SELECT b.*, u.username
        FROM butterflies b
        JOIN users u ON b.user_id = u.id
        WHERE b.id = ?
    ''', (butterfly_id,), one=True)
    if not b: return jsonify({'error':'Not found'}), 404
    # inclui user info
    return jsonify({
        'id': b['id'],
        'username': b['username'],
        'base_species': b['base_species'],
        'generation': b['generation'],
        'created_at': b['created_at'],
        'stage': b['stage'],
        'traits': b['traits'],
        'rare': b['rare'],
        'parents': b['parents'].split(',') if b['parents'] else None
    })

# --- ===== NOVO: CONQUISTAS ===== ---
def unlock_achievement_simple(user_id, key, name):
    db = get_db()
    try:
        db.execute('INSERT OR IGNORE INTO achievements (user_id, key, unlocked_at) VALUES (?, ?, ?)',
                   (user_id, key, datetime.utcnow().isoformat()))
        db.commit()
    except Exception:
        pass  # já desbloqueada

@app.route('/api/achievements', methods=['GET'])
@require_auth
def get_achievements():
    """Lista todas as conquistas do usuário logado."""
    achs = query_db('SELECT key, unlocked_at FROM achievements WHERE user_id = ? ORDER BY unlocked_at DESC', (g.user_id,))
    # Mapa de chave → descrição (pode ser expandido)
    meta = {
        'primeira_criacao': {'name':'Primeira Criação','desc':'Criou sua primeira borboleta'},
        'adulto': {'name':'Adulto!','desc':'Levou uma borboleta até a fase adulta'},
        'mutante': {'name':'Mutante Rara','desc':'Criou uma borboleta rara por mutação'},
        'acasalou': {'name':'Reprodução','desc':'Fez duas borboletas acasalarem'},
        'dez_adultas': {'name':'Coleção','desc':'Chegou a 10 borboletas adultas'},
        'raridade_top10': {'name':'Top 10 Rara','desc':'Sua espécie está entre as 10 mais raras do mundo'}
    }
    # Marcar quais o usuário tem
    user_keys = set(a['key'] for a in achs)
    # Também retorna meta completa
    full = []
    for k,m in meta.items():
        full.append({
            'key':k,
            'name':m['name'],
            'desc':m['desc'],
            'unlocked': k in user_keys,
            'unlocked_at': next((a['unlocked_at'] for a in achs if a['key']==k), None)
        })
    return jsonify(full)

@app.route('/api/butterflies/<butterfly_id>/share', methods=['GET'])
@require_auth
def get_share_link(butterfly_id):
    """Retorna URL compartilhável (frontend monta o link completo)."""
    # Valida se a borboleta pertence ao usuário
    b = query_db('SELECT * FROM butterflies WHERE id = ? AND user_id = ?', (butterfly_id, g.user_id), one=True)
    if not b: return jsonify({'error':'Not found'}), 404
    # Retorna apenas o ID (frontend monta o link)
    return jsonify({'butterfly_id':butterfly_id})

# --- Inicialização ---
@app.before_first_request
def setup():
    init_db()

if __name__ == '__main__':
    app.run(port=5000, debug=True)
