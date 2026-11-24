from datetime import datetime, timedelta
import secrets
from backend.db import connect
from backend.security import hash_password, verify_password, token_hash
from config import SESSION_HOURS

ROLES = ['admin','student','teacher','grade_manager','financial','disciplinary','educational','cultural','research','media']
ADMIN_PERMS = ['announcement','blog','schedule','daily','studentForm','exam','classReport','reportCard','responsibility','manageAccounts','layout','questionBank','classManagement','media','statistics','backupManagement']

def create_initial_admin(username, password, full_name='مدیر سامانه'):
    with connect() as db:
        if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            raise RuntimeError('حساب کاربری از قبل وجود دارد؛ ساخت مدیر اولیه مجاز نیست.')
        cur = db.execute('INSERT INTO users(username,password_hash,full_name,role,must_change_password) VALUES(?,?,?,?,1)',
                         (username, hash_password(password), full_name, 'admin'))
        uid = cur.lastrowid
        db.executemany('INSERT INTO user_permissions(user_id,permission_code) VALUES(?,?)', [(uid,p) for p in ADMIN_PERMS])
        db.execute('INSERT INTO audit_logs(user_id,action,detail) VALUES(?,?,?)',(uid,'initial_admin_created',username))

def login(username, password):
    now = datetime.utcnow()
    with connect() as db:
        lock = db.execute('SELECT attempts,locked_until FROM failed_logins WHERE username=?',(username,)).fetchone()
        if lock and lock['locked_until'] and lock['locked_until'] > now.isoformat(timespec='seconds'):
            return None
        user = db.execute('SELECT * FROM users WHERE username=? AND active=1',(username,)).fetchone()
        if not user or not verify_password(password, user['password_hash']):
            attempts = (lock['attempts'] if lock else 0) + 1
            locked_until = (now+timedelta(minutes=10)).isoformat(timespec='seconds') if attempts >= 5 else None
            db.execute('INSERT INTO failed_logins(username,attempts,locked_until) VALUES(?,?,?) ON CONFLICT(username) DO UPDATE SET attempts=excluded.attempts,locked_until=excluded.locked_until',(username,attempts,locked_until))
            return None
        db.execute('DELETE FROM failed_logins WHERE username=?',(username,))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        expiry = (now+timedelta(hours=SESSION_HOURS)).isoformat(timespec='seconds')
        db.execute('INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at) VALUES(?,?,?,?)',
                   (token_hash(token),user['id'],csrf,expiry))
        db.execute('INSERT INTO audit_logs(user_id,action) VALUES(?,?)',(user['id'],'login'))
        return token, csrf

def current_user(token):
    if not token: return None
    with connect() as db:
        row = db.execute('''SELECT u.*,s.csrf_token,s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id
                            WHERE s.token_hash=? AND u.active=1''',(token_hash(token),)).fetchone()
        if not row: return None
        if row['expires_at'] < datetime.utcnow().isoformat(timespec='seconds'):
            db.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(token),)); return None
        perms = [x['permission_code'] for x in db.execute('SELECT permission_code FROM user_permissions WHERE user_id=?',(row['id'],))]
        return {'id':row['id'],'username':row['username'],'full_name':row['full_name'],'role':row['role'],
                'grade':row['grade'],'class_name':row['class_name'],'must_change_password':bool(row['must_change_password']),
                'csrf_token':row['csrf_token'],'permissions':perms}

def logout(token):
    if token:
        with connect() as db: db.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(token),))

def allowed(user, permission):
    return bool(user and (user['role']=='admin' or permission in user['permissions']))
