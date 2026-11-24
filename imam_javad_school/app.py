from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import os
import json, secrets, logging, mimetypes, base64, uuid
from config import HOST, PORT, BASE_DIR, MAX_BODY, LOG_DIR, UPLOAD_DIR
from backend.db import init_db, connect
from backend.auth import login, current_user, logout, allowed, ROLES
from backend.security import hash_password
from backend.timeutil import tehran_naive, parse_tehran_datetime
from backend import domain

LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(filename=str(LOG_DIR/'server.log'), level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
INDEX = BASE_DIR/'frontend'/'index.html'

def safe_json(handler):
    n=int(handler.headers.get('Content-Length','0'))
    if n<0 or n>MAX_BODY: raise ValueError('حجم درخواست بیش از حد مجاز است')
    raw=handler.rfile.read(n) if n else b'{}'
    obj=json.loads(raw.decode('utf-8'))
    if not isinstance(obj,dict): raise ValueError('ساختار درخواست نامعتبر است')
    return obj

class Handler(BaseHTTPRequestHandler):
    server_version='ImamJavadLocal/1.0'
    def log_message(self, fmt, *args): logging.info('%s - %s', self.address_string(), fmt%args)
    def send_json(self, obj, status=200, headers=None):
        data=json.dumps(obj,ensure_ascii=False).encode('utf-8')
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(data))); self.send_header('Cache-Control','no-store')
        self.security_headers()
        if headers:
            for k,v in headers.items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(data)
    def security_headers(self):
        self.send_header('X-Content-Type-Options','nosniff'); self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','same-origin'); self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
    def get_token(self):
        for part in self.headers.get('Cookie','').split(';'):
            k,_,v=part.strip().partition('=')
            if k=='school_session': return v
        return None
    def require_user(self, permission=None, csrf=False):
        u=current_user(self.get_token())
        if not u: self.send_json({'error':'ابتدا وارد سامانه شوید'},401); return None
        if u.get('must_change_password') and urlparse(self.path).path not in ('/api/me','/api/change-password','/api/logout'):
            self.send_json({'error':'برای ادامه ابتدا باید رمز عبور اولیه را تغییر دهید'},428); return None
        if csrf and not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),u['csrf_token']):
            self.send_json({'error':'درخواست نامعتبر است'},403); return None
        if permission and not allowed(u,permission): self.send_json({'error':'مجوز انجام این عملیات را ندارید'},403); return None
        return u
    def domain_permission(self, resource, user):
        if resource == 'activities': return user['role']=='student' or allowed(user,'studentForm')
        if resource == 'report-cards': return user['role']=='student' or allowed(user,'reportCard')
        if resource == 'attempts': return user['role']=='student' or allowed(user,'exam')
        if resource == 'media': return allowed(user,'media')
        if resource in ('stats','settings','audit','backups'): return resource=='stats' or (resource=='settings' and allowed(user,'layout')) or (resource=='audit' and user['role']=='admin') or (resource=='backups' and allowed(user,'backupManagement'))
        if resource=='import-users-csv': return allowed(user,'manageAccounts')
        if resource not in domain.RESOURCES: return False
        return allowed(user, domain.RESOURCES[resource][1]) or (user['role']=='student' and resource in ('schedules','exams','class-reports','responsibilities','daily'))

    def domain_get(self, path, user):
        parts=path.strip('/').split('/')
        if len(parts)<3 or parts[1]!='data': return False
        resource=parts[2]
        if not self.domain_permission(resource,user): self.send_json({'error':'مجوز مشاهده این بخش را ندارید'},403); return True
        query=parse_qs(urlparse(self.path).query).get('q',[''])[0]
        try:
            if resource=='exams' and len(parts)==5 and parts[4]=='questions' and parts[3].isdigit():
                with connect() as db: rows=db.execute('SELECT id,position,points,question_text,question_type,options_json FROM exam_questions WHERE exam_id=? ORDER BY position',(int(parts[3]),)).fetchall()
                items=[dict(r) for r in rows]
            elif resource=='activities': items=domain.get_activities(user)
            elif resource=='report-cards': items=domain.get_report_cards(user)
            elif resource in domain.RESOURCES: items=domain.list_resource(resource,user,query)
            elif resource=='attempts' and len(parts)==5 and parts[4]=='answers':
                attempt_id=int(parts[3])
                with connect() as db:
                    if user['role']=='student':
                        att=db.execute('SELECT * FROM exam_attempts WHERE id=? AND student_user_id=?',(attempt_id,user['id'])).fetchone()
                    else:
                        att=db.execute('SELECT a.*,e.teacher_id FROM exam_attempts a JOIN exams e ON e.id=a.exam_id WHERE a.id=?',(attempt_id,)).fetchone()
                        if att and user['role']!='admin' and att['teacher_id']!=user['id']: att=None
                    if not att: self.send_json({'error':'پاسخ‌نامه پیدا نشد'},404); return True
                    rows=db.execute('SELECT ea.id answer_id,ea.answer,ea.score,eq.points,eq.question_text,eq.question_type FROM exam_answers ea JOIN exam_questions eq ON eq.id=ea.exam_question_id WHERE ea.attempt_id=?',(attempt_id,)).fetchall()
                items=[dict(r) for r in rows]
            elif resource=='attempts':
                with connect() as db:
                    if user['role']=='student':
                        rows=db.execute('SELECT a.id,a.exam_id,e.title exam_title,a.started_at,a.submitted_at,a.score,a.status FROM exam_attempts a JOIN exams e ON e.id=a.exam_id WHERE a.student_user_id=? ORDER BY a.id DESC',(user['id'],)).fetchall()
                    elif user['role']=='admin':
                        rows=db.execute('SELECT a.id,a.exam_id,e.title exam_title,u.full_name student_name,a.started_at,a.submitted_at,a.score,a.status FROM exam_attempts a JOIN exams e ON e.id=a.exam_id JOIN users u ON u.id=a.student_user_id ORDER BY a.id DESC LIMIT 500').fetchall()
                    else:
                        rows=db.execute('SELECT a.id,a.exam_id,e.title exam_title,u.full_name student_name,a.started_at,a.submitted_at,a.score,a.status FROM exam_attempts a JOIN exams e ON e.id=a.exam_id JOIN users u ON u.id=a.student_user_id WHERE e.teacher_id=? ORDER BY a.id DESC LIMIT 500',(user['id'],)).fetchall()
                items=[dict(r) for r in rows]
            elif resource=='stats':
                with connect() as db:
                    items={'students':db.execute("SELECT COUNT(*) FROM users WHERE role='student' AND active=1").fetchone()[0], 'teachers':db.execute("SELECT COUNT(*) FROM users WHERE role='teacher' AND active=1").fetchone()[0], 'classes':db.execute('SELECT COUNT(*) FROM classes WHERE active=1').fetchone()[0], 'exams':db.execute('SELECT COUNT(*) FROM exams').fetchone()[0], 'announcements':db.execute('SELECT COUNT(*) FROM announcements').fetchone()[0]}
            elif resource=='settings':
                if not allowed(user,'layout'): self.send_json({'error':'مجوز ندارید'},403); return True
                with connect() as db: items={r['key']:r['value'] for r in db.execute('SELECT key,value FROM school_settings')}
            elif resource=='media':
                with connect() as db: items=[dict(r) for r in db.execute('SELECT id,original_name,mime_type,size_bytes,created_at,stored_name FROM media_files ORDER BY id DESC LIMIT 200')]
            elif resource=='backups':
                if not allowed(user,'backupManagement'): self.send_json({'error':'مجوز ندارید'},403); return True
                with connect() as db: items=[dict(r) for r in db.execute('SELECT * FROM backup_records ORDER BY id DESC LIMIT 50')]
            elif resource=='audit':
                if user['role']!='admin': self.send_json({'error':'فقط مدیر مجاز است'},403); return True
                with connect() as db: items=[dict(r) for r in db.execute('SELECT a.*,u.full_name FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 300')]
            else: self.send_json({'error':'بخش پیدا نشد'},404); return True
            self.send_json({'items':items}); return True
        except PermissionError as e: self.send_json({'error':str(e)},403); return True
        except Exception: logging.exception('domain GET failed'); self.send_json({'error':'خطا در دریافت اطلاعات'},500); return True

    def domain_post(self, path, body, user):
        parts=path.strip('/').split('/')
        if len(parts)<3 or parts[1]!='data': return False
        resource=parts[2]
        if resource=='exams' and len(parts)==5 and parts[4]=='questions':
            if not allowed(user,'exam'): self.send_json({'error':'مجوز افزودن سؤال ندارید'},403); return True
            try:
                exam_id=int(parts[3]); qid=body.get('question_id')
                with connect() as db:
                    if qid:
                        q=db.execute('SELECT * FROM question_bank WHERE id=?',(int(qid),)).fetchone()
                        if not q: raise ValueError('سؤال بانک پیدا نشد')
                        pos=db.execute('SELECT COALESCE(MAX(position),0)+1 FROM exam_questions WHERE exam_id=?',(exam_id,)).fetchone()[0]
                        db.execute('INSERT INTO exam_questions(exam_id,question_id,position,points,question_text,question_type,options_json,answer) VALUES(?,?,?,?,?,?,?,?)',(exam_id,qid,pos,float(body.get('points',1)),q['body'],q['question_type'],q['options_json'],q['answer']))
                    else:
                        pos=int(body.get('position',1)); db.execute('INSERT INTO exam_questions(exam_id,position,points,question_text,question_type,options_json,answer) VALUES(?,?,?,?,?,?,?)',(exam_id,pos,float(body.get('points',1)),str(body.get('question_text',''))[:5000],str(body.get('question_type','essay')),json.dumps(body.get('options',[]),ensure_ascii=False),str(body.get('answer',''))[:1000]))
                self.send_json({'success':True},201)
            except Exception as e:self.send_json({'error':str(e)},400)
            return True
        if resource=='exams' and len(parts)==5 and parts[4]=='start':
            if user['role']!='student': self.send_json({'error':'فقط دانش‌آموز می‌تواند آزمون را آغاز کند'},403); return True
            try:
                with connect() as db:
                    exam=db.execute("SELECT * FROM exams WHERE id=? AND status='published' AND grade=? AND (class_name IS NULL OR class_name='' OR class_name=?)",(int(parts[3]),user.get('grade'),user.get('class_name'))).fetchone()
                    if not exam: raise ValueError('آزمون در دسترس نیست')
                    from datetime import datetime
                    now=tehran_naive()
                    for field in ('starts_at','ends_at'):
                        value=exam[field]
                        if value:
                            try: boundary=parse_tehran_datetime(value)
                            except ValueError: raise ValueError('تاریخ شروع یا پایان آزمون معتبر نیست')
                            if field=='starts_at' and now < boundary: raise ValueError('زمان شروع آزمون هنوز نرسیده است')
                            if field=='ends_at' and now > boundary: raise ValueError('مهلت آزمون پایان یافته است')
                    try:
                        cur=db.execute('INSERT INTO exam_attempts(exam_id,student_user_id) VALUES(?,?)',(exam['id'],user['id']))
                        attempt=cur.lastrowid
                    except Exception:
                        existing=db.execute("SELECT id FROM exam_attempts WHERE exam_id=? AND student_user_id=? AND status='in_progress'",(exam['id'],user['id'])).fetchone()
                        if not existing: raise
                        attempt=existing['id']
                    started=db.execute('SELECT started_at FROM exam_attempts WHERE id=?',(attempt,)).fetchone()['started_at']
                self.send_json({'attempt_id':attempt,'exam_id':exam['id'],'started_at':started,'duration_minutes':exam['duration_minutes']},201)
            except Exception as e:self.send_json({'error':'آزمون قبلاً آغاز شده یا در دسترس نیست'},400)
            return True
        if resource=='attempts' and len(parts)==5 and parts[4]=='grade':
            if not allowed(user,'exam'): self.send_json({'error':'مجوز تصحیح آزمون ندارید'},403); return True
            try:
                attempt_id=int(parts[3]); scores=body.get('scores',[])
                if not isinstance(scores,list): raise ValueError('نمرات نامعتبرند')
                total=0.0
                with connect() as db:
                    att=db.execute('SELECT a.*,e.teacher_id,e.total_score FROM exam_attempts a JOIN exams e ON e.id=a.exam_id WHERE a.id=?',(attempt_id,)).fetchone()
                    if not att: raise ValueError('پاسخ‌نامه پیدا نشد')
                    if user['role']!='admin' and att['teacher_id']!=user['id']: raise PermissionError('فقط دبیر سازنده آزمون یا مدیر می‌تواند تصحیح کند')
                    for item in scores:
                        aid=int(item.get('answer_id')); score=float(item.get('score',0))
                        row=db.execute('SELECT ea.id,eq.points,eq.question_type FROM exam_answers ea JOIN exam_questions eq ON eq.id=ea.exam_question_id WHERE ea.id=? AND ea.attempt_id=?',(aid,attempt_id)).fetchone()
                        if not row or row['question_type'] not in ('essay','short'): raise ValueError('پاسخ تشریحی معتبر نیست')
                        if score<0 or score>float(row['points']): raise ValueError('نمره پاسخ خارج از محدوده است')
                        db.execute('UPDATE exam_answers SET score=?,is_correct=1 WHERE id=?',(score,aid))
                    total=db.execute('SELECT COALESCE(SUM(score),0) FROM exam_answers WHERE attempt_id=?',(attempt_id,)).fetchone()[0]
                    total=max(0.0,min(float(att['total_score'] or total),float(total)))
                    pending=db.execute('SELECT COUNT(*) FROM exam_answers WHERE attempt_id=? AND is_correct IS NULL AND score=0',(attempt_id,)).fetchone()[0]
                    db.execute("UPDATE exam_attempts SET score=?,status=? WHERE id=?",(total,'needs_review' if pending else 'graded',attempt_id))
                    db.execute('INSERT INTO audit_logs(user_id,action,detail) VALUES(?,?,?)',(user['id'],'grade_exam',str(attempt_id)))
                self.send_json({'success':True,'score':total})
            except PermissionError as e:self.send_json({'error':str(e)},403)
            except Exception as e:self.send_json({'error':str(e)},400)
            return True
        if resource=='attempts' and len(parts)==5 and parts[4]=='answers':
            if not allowed(user,'exam') and user['role']!='student': self.send_json({'error':'مجوز مشاهده پاسخ‌نامه ندارید'},403); return True
            with connect() as db:
                if user['role']=='student':
                    att=db.execute('SELECT * FROM exam_attempts WHERE id=? AND student_user_id=?',(int(parts[3]),user['id'])).fetchone()
                else:
                    att=db.execute('SELECT a.*,e.teacher_id FROM exam_attempts a JOIN exams e ON e.id=a.exam_id WHERE a.id=?',(int(parts[3]),)).fetchone()
                    if att and user['role']!='admin' and att['teacher_id']!=user['id']: att=None
                if not att: self.send_json({'error':'پاسخ‌نامه پیدا نشد'},404); return True
                rows=db.execute('SELECT ea.id answer_id,ea.answer,ea.score,eq.points,eq.question_text,eq.question_type FROM exam_answers ea JOIN exam_questions eq ON eq.id=ea.exam_question_id WHERE ea.attempt_id=?',(int(parts[3]),)).fetchall()
            self.send_json({'items':[dict(r) for r in rows]}); return True
        if resource=='attempts' and len(parts)==5 and parts[4]=='submit':
            if user['role']!='student': self.send_json({'error':'فقط دانش‌آموز می‌تواند پاسخ را ثبت کند'},403); return True
            try:
                answers=body.get('answers',[])
                if not isinstance(answers,list): raise ValueError('پاسخ‌ها نامعتبرند')
                score=0.0; essay=False
                with connect() as db:
                    att=db.execute("SELECT * FROM exam_attempts WHERE id=? AND student_user_id=? AND status='in_progress'",(int(parts[3]),user['id'])).fetchone()
                    if not att: raise ValueError('آزمون فعال پیدا نشد')
                    qs=db.execute('SELECT * FROM exam_questions WHERE exam_id=?',(att['exam_id'],)).fetchall()
                    amap={int(a.get('question_id')):str(a.get('answer',''))[:5000] for a in answers if a.get('question_id') is not None}
                    allowed_ids={int(q['id']) for q in qs}
                    if any(k not in allowed_ids for k in amap): raise ValueError('پاسخ شامل سؤال نامعتبر است')
                    exam=db.execute('SELECT total_score,negative_score,duration_minutes,ends_at FROM exams WHERE id=?',(att['exam_id'],)).fetchone()
                    from datetime import datetime
                    started=parse_tehran_datetime(att['started_at'])
                    elapsed=(datetime.utcnow()-started).total_seconds()/60
                    timed_out=elapsed > int(exam['duration_minutes'] or 30)
                    if exam['ends_at']:
                        try: timed_out = timed_out or tehran_naive() > parse_tehran_datetime(exam['ends_at'])
                        except ValueError: pass
                    for q in qs:
                        ans=amap.get(q['id'],''); typ=q['question_type']; correct=None; pts=0
                        if typ in ('mcq','truefalse','blank','short') and q['answer'] is not None:
                            correct=(ans.strip().casefold()==str(q['answer']).strip().casefold())
                            pts=float(q['points']) if correct else (-float(exam['negative_score'] or 0) if ans else 0)
                            score+=pts
                        else: essay=True
                        db.execute('INSERT INTO exam_answers(attempt_id,exam_question_id,answer,is_correct,score) VALUES(?,?,?,?,?)',(att['id'],q['id'],ans,None if correct is None else int(correct),pts))
                    score=max(0.0,min(float(exam['total_score'] or score),score))
                    status='timed_out' if timed_out else ('needs_review' if essay else 'submitted')
                    db.execute("UPDATE exam_attempts SET submitted_at=CURRENT_TIMESTAMP,score=?,status=? WHERE id=?",(score,status,att['id']))
                    db.execute('INSERT INTO audit_logs(user_id,action,detail) VALUES(?,?,?)',(user['id'],'submit_exam',str(att['exam_id'])))
                self.send_json({'success':True,'score':score,'needs_review':essay,'timed_out':timed_out})
            except Exception as e:self.send_json({'error':str(e)},400)
            return True
        if resource=='activities':
            if user['role']!='student' and not allowed(user,'studentForm'): self.send_json({'error':'مجوز ندارید'},403); return True
            try: rid=domain.save_activity(body,user); self.send_json({'success':True,'id':rid},201)
            except PermissionError as e: self.send_json({'error':str(e)},403)
            except Exception as e: self.send_json({'error':str(e)},400)
            return True
        if resource=='report-cards':
            if not allowed(user,'reportCard'): self.send_json({'error':'مجوز ندارید'},403); return True
            try: rid=domain.save_report_card(body,user); self.send_json({'success':True,'id':rid},201)
            except Exception as e: self.send_json({'error':str(e)},400)
            return True
        if resource=='settings':
            if not allowed(user,'layout'): self.send_json({'error':'مجوز ندارید'},403); return True
            try:
                with connect() as db:
                    for k,v in body.items(): db.execute('INSERT INTO school_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(str(k)[:80],str(v)[:4000]))
                    db.execute('INSERT INTO audit_logs(user_id,action,detail) VALUES(?,?,?)',(user['id'],'update_settings','school_settings'))
                self.send_json({'success':True})
            except Exception: self.send_json({'error':'ذخیره تنظیمات ناموفق بود'},400)
            return True
        if resource=='media':
            if not allowed(user,'media'): self.send_json({'error':'مجوز بارگذاری رسانه ندارید'},403); return True
            try:
                name=str(body.get('filename','file'))[:180]; mime=str(body.get('mime_type','')); raw=base64.b64decode(str(body.get('data_b64','')).encode('ascii'),validate=True)
                if len(raw)>5*1024*1024: raise ValueError('حداکثر حجم فایل ۵ مگابایت است')
                ext=Path(name).suffix.lower(); allowed_types={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.webp':'image/webp','.gif':'image/gif','.pdf':'application/pdf'}
                if ext not in allowed_types or mime!=allowed_types[ext]: raise ValueError('نوع فایل مجاز نیست')
                if ext in ('.jpg','.jpeg') and not raw.startswith(b'\xff\xd8\xff'): raise ValueError('محتوای تصویر معتبر نیست')
                if ext=='.png' and not raw.startswith(b'\x89PNG\r\n\x1a\n'): raise ValueError('محتوای تصویر معتبر نیست')
                if ext=='.pdf' and not raw.startswith(b'%PDF-'): raise ValueError('محتوای PDF معتبر نیست')
                if ext=='.webp' and not (raw.startswith(b'RIFF') and raw[8:12]==b'WEBP'): raise ValueError('محتوای تصویر معتبر نیست')
                if ext=='.gif' and not raw.startswith((b'GIF87a',b'GIF89a')): raise ValueError('محتوای تصویر معتبر نیست')
                stored=uuid.uuid4().hex+ext; folder=UPLOAD_DIR/('images' if mime.startswith('image/') else 'documents'); folder.mkdir(parents=True,exist_ok=True); (folder/stored).write_bytes(raw)
                with connect() as db: db.execute('INSERT INTO media_files(original_name,stored_name,mime_type,size_bytes,uploaded_by) VALUES(?,?,?,?,?)',(Path(name).name,stored,mime,len(raw),user['id']))
                self.send_json({'success':True,'stored_name':stored},201)
            except Exception as e:self.send_json({'error':str(e)},400)
            return True
        if resource=='backups':
            if not allowed(user,'backupManagement'): self.send_json({'error':'مجوز ندارید'},403); return True
            try:
                from backup import create_backup
                target=create_backup(); self.send_json({'success':True,'filename':target.name,'size':target.stat().st_size},201)
            except Exception: self.send_json({'error':'تهیه نسخه پشتیبان ناموفق بود'},500)
            return True
        if resource=='import-users-csv':
            if not allowed(user,'manageAccounts'): self.send_json({'error':'مجوز ندارید'},403); return True
            try: self.send_json(domain.import_users_csv(str(body.get('csv_text','')),user,bool(body.get('commit',False))),201)
            except Exception as e: self.send_json({'error':str(e)},400)
            return True
        if resource=='import-users-xlsx':
            if not allowed(user,'manageAccounts'): self.send_json({'error':'مجوز ندارید'},403); return True
            try: self.send_json(domain.import_users_xlsx_b64(str(body.get('file_b64','')),user,bool(body.get('commit',False))),201)
            except Exception as e:self.send_json({'error':str(e)},400)
            return True
        if resource in domain.RESOURCES:
            if not allowed(user,domain.RESOURCES[resource][1]): self.send_json({'error':'مجوز ایجاد اطلاعات را ندارید'},403); return True
            try: rid=domain.create_resource(resource,body,user); self.send_json({'success':True,'id':rid},201)
            except Exception as e: self.send_json({'error':str(e)},400)
            return True
        self.send_json({'error':'بخش پیدا نشد'},404); return True

    def do_GET(self):
        path=urlparse(self.path).path
        if path.startswith('/api/data/'):
            u=self.require_user()
            if u: self.domain_get(path,u)
            return
        if path=='/':
            data=INDEX.read_bytes(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.security_headers(); self.end_headers(); self.wfile.write(data); return
        if path.startswith('/media/'):
            u=self.require_user()
            if not u:return
            name=Path(path.split('/')[-1]).name
            with connect() as db: row=db.execute('SELECT * FROM media_files WHERE stored_name=?',(name,)).fetchone()
            if not row: self.send_error(404); return
            folder=UPLOAD_DIR/('images' if row['mime_type'].startswith('image/') else 'documents'); target=(folder/name).resolve()
            if not target.is_file(): self.send_error(404); return
            data=target.read_bytes(); self.send_response(200); self.send_header('Content-Type',row['mime_type']); self.send_header('Content-Length',str(len(data))); self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Content-Disposition','attachment' if row['mime_type']=='application/pdf' else 'inline'); self.end_headers(); self.wfile.write(data); return
        if path.startswith('/static/'):
            rel=path[len('/static/'):]
            target=(BASE_DIR/'frontend'/rel).resolve()
            if os.path.commonpath([str(target),str((BASE_DIR/'frontend').resolve())]) != str((BASE_DIR/'frontend').resolve()) or not target.is_file(): self.send_error(404); return
            data=target.read_bytes(); self.send_response(200); self.send_header('Content-Type',mimetypes.guess_type(str(target))[0] or 'application/octet-stream'); self.send_header('Content-Length',str(len(data))); self.security_headers(); self.end_headers(); self.wfile.write(data); return
        if path=='/api/me':
            u=self.require_user()
            if u: self.send_json({'user':{k:v for k,v in u.items() if k!='csrf_token'},'csrf_token':u['csrf_token']})
            return
        if path=='/api/announcements':
            u=self.require_user()
            if not u:return
            with connect() as db:
                if u['role']=='student':
                    rows=db.execute("SELECT a.id,a.title,a.body,a.scope,a.grade,a.class_name,a.created_at,u.full_name author FROM announcements a LEFT JOIN users u ON u.id=a.author_id WHERE a.scope='school' OR (a.scope='grade' AND a.grade=?) OR (a.scope='class' AND a.grade=? AND a.class_name=?) ORDER BY a.id DESC",(u['grade'],u['grade'],u['class_name'])).fetchall()
                else: rows=db.execute('SELECT a.id,a.title,a.body,a.scope,a.grade,a.class_name,a.created_at,u.full_name author FROM announcements a LEFT JOIN users u ON u.id=a.author_id ORDER BY a.id DESC').fetchall()
            self.send_json({'items':[dict(r) for r in rows]}); return
        if path=='/api/blogs':
            u=self.require_user()
            if not u:return
            with connect() as db:
                rows=db.execute('SELECT b.id,b.title,b.body,b.category,b.scope,b.grade,b.class_name,b.created_at,u.full_name author FROM blogs b LEFT JOIN users u ON u.id=b.author_id ORDER BY b.id DESC').fetchall()
            items=[dict(r) for r in rows]
            if u['role']=='student': items=[x for x in items if x['scope']=='school' or (x['scope']=='grade' and x['grade']==u['grade']) or (x['scope']=='class' and x['grade']==u['grade'] and x['class_name']==u['class_name'])]
            self.send_json({'items':items}); return
        if path=='/api/users/export.csv':
            u=self.require_user('manageAccounts')
            if not u:return
            import csv, io
            with connect() as db: rows=db.execute('SELECT username,full_name,role,grade,class_name,active,created_at FROM users ORDER BY id').fetchall()
            out=io.StringIO(); w=csv.writer(out); w.writerow(['username','full_name','role','grade','class_name','active','created_at'])
            for row in rows: w.writerow([str(v).replace('=','') if isinstance(v,str) and v.startswith(('=','+','-','@')) else v for v in row])
            raw=out.getvalue().encode('utf-8-sig'); self.send_response(200); self.send_header('Content-Type','text/csv; charset=utf-8'); self.send_header('Content-Disposition','attachment; filename=school-users.csv'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if path=='/api/users':
            u=self.require_user('manageAccounts')
            if not u:return
            with connect() as db:
                rows=db.execute('SELECT id,username,full_name,role,grade,class_name,active,must_change_password,created_at FROM users ORDER BY id DESC').fetchall()
                perms=db.execute('SELECT code,title FROM permissions ORDER BY id').fetchall()
                items=[]
                for row in rows:
                    item=dict(row); item['permissions']=[x['permission_code'] for x in db.execute('SELECT permission_code FROM user_permissions WHERE user_id=?',(row['id'],))]; items.append(item)
            self.send_json({'items':items,'permissions':[dict(r) for r in perms],'roles':ROLES}); return
        if path=='/api/classes':
            u=self.require_user('classManagement')
            if not u:return
            with connect() as db: rows=db.execute('SELECT id,grade,name,active FROM classes ORDER BY grade,name').fetchall()
            self.send_json({'items':[dict(r) for r in rows]}); return
        if path=='/api/dashboard':
            u=self.require_user()
            if not u:return
            with connect() as db:
                counts={'announcements':db.execute('SELECT COUNT(*) FROM announcements').fetchone()[0], 'blogs':db.execute('SELECT COUNT(*) FROM blogs').fetchone()[0]}
                if u['role']=='admin': counts['users']=db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
            self.send_json({'counts':counts}); return
        self.send_json({'error':'مسیر پیدا نشد'},404)
    def do_POST(self):
        path=urlparse(self.path).path
        try: body=safe_json(self)
        except Exception as e: self.send_json({'error':str(e)},400); return
        if path.startswith('/api/data/'):
            u=self.require_user(csrf=True)
            if u: self.domain_post(path,body,u)
            return
        if path=='/api/login':
            result=login(str(body.get('username','')).strip(),str(body.get('password','')))
            if not result: self.send_json({'error':'نام کاربری یا رمز عبور اشتباه است'},401); return
            token,csrf=result
            self.send_json({'success':True,'csrf_token':csrf},headers={'Set-Cookie':f'school_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800'})
            return
        if path=='/api/logout':
            u=self.require_user(csrf=True)
            if not u:return
            logout(self.get_token()); self.send_json({'success':True},headers={'Set-Cookie':'school_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'}); return
        if path=='/api/change-password':
            u=self.require_user(csrf=True)
            if not u:return
            new=str(body.get('new_password',''))
            if len(new)<10: self.send_json({'error':'رمز جدید باید حداقل ۱۰ نویسه داشته باشد'},400); return
            with connect() as db:
                db.execute('UPDATE users SET password_hash=?,must_change_password=0 WHERE id=?',(hash_password(new),u['id']))
                db.execute('DELETE FROM sessions WHERE user_id=?',(u['id'],))
                db.execute('INSERT INTO audit_logs(user_id,action,detail) VALUES(?,?,?)',(u['id'],'change_password','self'))
            self.send_json({'success':True},headers={'Set-Cookie':'school_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'}); return
        if path=='/api/users':
            u=self.require_user('manageAccounts',csrf=True)
            if not u:return
            username=str(body.get('username','')).strip(); full_name=str(body.get('full_name','')).strip()
            password=str(body.get('password','')); role=str(body.get('role','student'))
            grade=body.get('grade') or None; class_name=str(body.get('class_name','')).strip() or None
            permissions=body.get('permissions',[])
            if not username or len(username)>60 or not full_name or len(full_name)>120 or len(password)<10 or role not in ROLES:
                self.send_json({'error':'نام کاربری، نام کامل، نقش یا رمز عبور معتبر نیست (رمز حداقل ۱۰ نویسه)'},400); return
            if role=='student' and (grade not in ('هفتم','هشتم','نهم') or not class_name):
                self.send_json({'error':'برای دانش‌آموز، پایه و نام کلاس الزامی است'},400); return
            if role=='admin' and u['role']!='admin': self.send_json({'error':'فقط مدیر اصلی می‌تواند مدیر دیگری بسازد'},403); return
            if not isinstance(permissions,list) or any(not isinstance(x,str) for x in permissions): self.send_json({'error':'فهرست مجوزها نامعتبر است'},400); return
            if u['role']!='admin' and not set(permissions).issubset(set(u['permissions'])): self.send_json({'error':'نمی‌توانید مجوزی بیش از دسترسی خودتان واگذار کنید'},403); return
            try:
                with connect() as db:
                    cur=db.execute('INSERT INTO users(username,password_hash,full_name,role,grade,class_name,must_change_password) VALUES(?,?,?,?,?,?,1)',(username,hash_password(password),full_name,role,grade,class_name))
                    uid=cur.lastrowid; valid={r['code'] for r in db.execute('SELECT code FROM permissions')}
                    db.executemany('INSERT OR IGNORE INTO user_permissions(user_id,permission_code) VALUES(?,?)',[(uid,x) for x in permissions if x in valid and isinstance(x,str)])
                    if role=='student':
                        names=full_name.split(' ',1); db.execute('INSERT INTO students(user_id,first_name,last_name,grade,class_name) VALUES(?,?,?,?,?)',(uid,names[0],names[1] if len(names)>1 else '',grade,class_name))
                    elif role=='teacher': db.execute('INSERT INTO teachers(user_id,full_name) VALUES(?,?)',(uid,full_name))
                    db.execute('INSERT INTO audit_logs(user_id,action,detail) VALUES(?,?,?)',(u['id'],'create_user',username))
            except Exception as e:
                if 'UNIQUE' in str(e): self.send_json({'error':'این نام کاربری قبلاً ثبت شده است'},409); return
                raise
            self.send_json({'success':True},201); return
        if path=='/api/classes':
            u=self.require_user('classManagement',csrf=True)
            if not u:return
            grade=str(body.get('grade','')); name=str(body.get('name','')).strip()
            if grade not in ('هفتم','هشتم','نهم') or not name or len(name)>40:
                self.send_json({'error':'پایه یا نام کلاس معتبر نیست'},400); return
            try:
                with connect() as db:
                    db.execute('INSERT INTO classes(grade,name) VALUES(?,?)',(grade,name))
                    db.execute('INSERT INTO audit_logs(user_id,action,detail) VALUES(?,?,?)',(u['id'],'create_class',grade+' '+name))
            except Exception as e:
                if 'UNIQUE' in str(e): self.send_json({'error':'این کلاس قبلاً ثبت شده است'},409); return
                raise
            self.send_json({'success':True},201); return
        if path in ('/api/announcements','/api/blogs'):
            kind='announcement' if path.endswith('announcements') else 'blog'
            u=self.require_user(kind,csrf=True)
            if not u:return
            title=str(body.get('title','')).strip(); content=str(body.get('body','')).strip()
            scope=str(body.get('scope','school'))
            if not title or not content or len(title)>180 or len(content)>20000 or scope not in ('school','grade','class'):
                self.send_json({'error':'اطلاعات واردشده معتبر نیست'},400); return
            grade=body.get('grade') if scope in ('grade','class') else None
            class_name=body.get('class_name') if scope=='class' else None
            if scope!='school' and grade not in ('هفتم','هشتم','نهم'):
                self.send_json({'error':'پایه انتخاب‌شده معتبر نیست'},400); return
            table='announcements' if kind=='announcement' else 'blogs'
            with connect() as db:
                db.execute(f'INSERT INTO {table}(title,body,scope,grade,class_name,author_id) VALUES(?,?,?,?,?,?)',(title,content,scope,grade,class_name,u['id']))
                db.execute('INSERT INTO audit_logs(user_id,action,detail) VALUES(?,?,?)',(u['id'],'create_'+kind,title[:150]))
            self.send_json({'success':True},201); return
        self.send_json({'error':'مسیر پیدا نشد'},404)
    def do_PUT(self):
        path=urlparse(self.path).path
        try: body=safe_json(self)
        except Exception as e: self.send_json({'error':str(e)},400); return
        u=self.require_user(csrf=True)
        if not u:return
        parts=path.strip('/').split('/')
        if len(parts)==3 and parts[0]=='api' and parts[1] in ('announcements','blogs') and parts[2].isdigit():
            kind='announcement' if parts[1]=='announcements' else 'blog'
            if not allowed(u,kind): self.send_json({'error':'مجوز ویرایش ندارید'},403); return
            title=str(body.get('title','')).strip(); content=str(body.get('body','')).strip(); scope=str(body.get('scope','school'))
            if not title or not content or scope not in ('school','grade','class') or len(title)>180 or len(content)>20000: self.send_json({'error':'اطلاعات نامعتبر است'},400); return
            grade=body.get('grade') if scope in ('grade','class') else None; cls=body.get('class_name') if scope=='class' else None
            if scope!='school' and grade not in ('هفتم','هشتم','نهم'): self.send_json({'error':'پایه نامعتبر است'},400); return
            with connect() as db: cur=db.execute('UPDATE '+parts[1]+' SET title=?,body=?,scope=?,grade=?,class_name=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(title,content,scope,grade,cls,int(parts[2]))); db.execute('INSERT INTO audit_logs(user_id,action,detail) VALUES(?,?,?)',(u['id'],'update_'+kind,parts[2]))
            self.send_json({'success':bool(cur.rowcount)},200 if cur.rowcount else 404); return
        if len(parts)==3 and parts[:2]==['api','classes'] and parts[2].isdigit():
            if not allowed(u,'classManagement'): self.send_json({'error':'مجوز ویرایش کلاس ندارید'},403); return
            grade=str(body.get('grade','')); name=str(body.get('name','')).strip()
            if grade not in ('هفتم','هشتم','نهم') or not name or len(name)>40: self.send_json({'error':'پایه یا نام کلاس معتبر نیست'},400); return
            try:
                with connect() as db:
                    cur=db.execute('UPDATE classes SET grade=?,name=? WHERE id=?',(grade,name,int(parts[2])))
                    db.execute('INSERT INTO audit_logs(user_id,action,detail) VALUES(?,?,?)',(u['id'],'update_class',str(parts[2])))
                self.send_json({'success':bool(cur.rowcount)},200 if cur.rowcount else 404)
            except Exception as e:self.send_json({'error':'نام کلاس تکراری است یا ویرایش ممکن نیست'},409)
            return
        if len(parts)==3 and parts[0]=='api' and parts[1]=='users' and parts[2].isdigit():
            if not allowed(u,'manageAccounts'): self.send_json({'error':'مجوز ویرایش حساب ندارید'},403); return
            target=int(parts[2]); role=str(body.get('role','student')); full=str(body.get('full_name','')).strip()
            if role not in ROLES or not full or len(full)>120: self.send_json({'error':'اطلاعات حساب نامعتبر است'},400); return
            if role=='admin' and u['role']!='admin': self.send_json({'error':'فقط مدیر اصلی می‌تواند نقش مدیر بدهد'},403); return
            if target==u['id'] and role!='admin': self.send_json({'error':'نمی‌توانید نقش مدیر فعال خودتان را تغییر دهید'},400); return
            if isinstance(body.get('permissions'),list) and u['role']!='admin' and not set(body['permissions']).issubset(set(u['permissions'])): self.send_json({'error':'نمی‌توانید مجوزی بیش از دسترسی خودتان واگذار کنید'},403); return
            try:
                with connect() as db:
                    old_user=db.execute('SELECT role,active FROM users WHERE id=?',(target,)).fetchone()
                    if not old_user: self.send_json({'error':'حساب پیدا نشد'},404); return
                    if old_user['role']=='admin' and old_user['active'] and role!='admin' and db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]<=1:
                        self.send_json({'error':'نقش آخرین مدیر فعال را نمی‌توان تغییر داد'},400); return
                    db.execute('UPDATE users SET full_name=?,role=?,grade=?,class_name=? WHERE id=?',(full,role,body.get('grade') or None,body.get('class_name') or None,target))
                    names=full.split(' ',1)
                    if role=='student': db.execute('INSERT INTO students(user_id,first_name,last_name,grade,class_name) VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET first_name=excluded.first_name,last_name=excluded.last_name,grade=excluded.grade,class_name=excluded.class_name,active=1',(target,names[0],names[1] if len(names)>1 else '',body.get('grade') or '',body.get('class_name') or ''))
                    else: db.execute('UPDATE students SET active=0 WHERE user_id=?',(target,))
                    if role=='teacher': db.execute('INSERT INTO teachers(user_id,full_name) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name,active=1',(target,full))
                    else: db.execute('UPDATE teachers SET active=0 WHERE user_id=?',(target,))
                    if isinstance(body.get('permissions'),list):
                        db.execute('DELETE FROM user_permissions WHERE user_id=?',(target,))
                        valid={r['code'] for r in db.execute('SELECT code FROM permissions')}
                        db.executemany('INSERT OR IGNORE INTO user_permissions(user_id,permission_code) VALUES(?,?)',[(target,x) for x in body['permissions'] if x in valid])
                    password=str(body.get('password',''))
                    if password:
                        if len(password)<10: raise ValueError('رمز باید حداقل ۱۰ نویسه باشد')
                        db.execute('UPDATE users SET password_hash=?,must_change_password=1 WHERE id=?',(hash_password(password),target))
                        db.execute('DELETE FROM sessions WHERE user_id=?',(target,))
                    db.execute('INSERT INTO audit_logs(user_id,action,detail) VALUES(?,?,?)',(u['id'],'edit_user',str(target)))
                self.send_json({'success':True})
            except Exception as e:self.send_json({'error':str(e)},400)
            return
        if len(parts)==4 and parts[:3]==['api','data','activities'] and parts[3].isdigit():
            try:
                ok=domain.update_activity(int(parts[3]),body,u)
                self.send_json({'success':ok},200 if ok else 404)
            except PermissionError as e:self.send_json({'error':str(e)},403)
            except Exception as e:self.send_json({'error':str(e)},400)
            return
        if len(parts)==4 and parts[0]=='api' and parts[1]=='data' and parts[2] in domain.RESOURCES and parts[3].isdigit():
            resource=parts[2]
            if not allowed(u,domain.RESOURCES[resource][1]): self.send_json({'error':'مجوز ویرایش ندارید'},403); return
            try:
                ok=domain.update_resource(resource,int(parts[3]),body,u)
                self.send_json({'success':ok},200 if ok else 404)
            except Exception as e:self.send_json({'error':str(e)},400)
            return
        self.send_json({'error':'مسیر پیدا نشد'},404)

    def do_DELETE(self):
        path=urlparse(self.path).path
        parts=path.strip('/').split('/')
        if len(parts)==3 and parts[0]=='api' and parts[1]=='media' and parts[2].isdigit():
            u=self.require_user(csrf=True)
            if not u:return
            if not allowed(u,'media'): self.send_json({'error':'مجوز حذف رسانه ندارید'},403); return
            with connect() as db: row=db.execute('SELECT stored_name,mime_type FROM media_files WHERE id=?',(int(parts[2]),)).fetchone(); db.execute('DELETE FROM media_files WHERE id=?',(int(parts[2]),))
            if row:
                folder=UPLOAD_DIR/('images' if row['mime_type'].startswith('image/') else 'documents'); (folder/row['stored_name']).unlink(missing_ok=True)
            self.send_json({'success':bool(row)}); return
        if len(parts)==4 and parts[:3]==['api','data','activities'] and parts[3].isdigit():
            try:
                ok=domain.update_activity(int(parts[3]),body,u)
                self.send_json({'success':ok},200 if ok else 404)
            except PermissionError as e:self.send_json({'error':str(e)},403)
            except Exception as e:self.send_json({'error':str(e)},400)
            return
        if len(parts)==4 and parts[:3]==['api','data','activities'] and parts[3].isdigit():
            u=self.require_user(csrf=True)
            if not u:return
            try:
                ok=domain.delete_activity(int(parts[3]),u); self.send_json({'success':ok},200 if ok else 404)
            except PermissionError as e:self.send_json({'error':str(e)},403)
            return
        if len(parts)==4 and parts[0]=='api' and parts[1]=='data' and parts[2] in domain.RESOURCES and parts[3].isdigit():
            u=self.require_user(csrf=True)
            if not u:return
            resource=parts[2]
            if not allowed(u,domain.RESOURCES[resource][1]): self.send_json({'error':'مجوز حذف ندارید'},403); return
            try:
                ok=domain.delete_resource(resource,int(parts[3]),u); self.send_json({'success':ok},200 if ok else 404)
            except Exception as e:self.send_json({'error':str(e)},400)
            return
        if len(parts)==3 and parts[0]=='api' and parts[1]=='users' and parts[2].isdigit():
            u=self.require_user('manageAccounts',csrf=True)
            if not u:return
            target=int(parts[2])
            if target==u['id']: self.send_json({'error':'نمی‌توانید حساب خودتان را غیرفعال کنید'},400); return
            with connect() as db:
                target_row=db.execute('SELECT role,active FROM users WHERE id=?',(target,)).fetchone()
                if not target_row: self.send_json({'error':'حساب پیدا نشد'},404); return
                if target_row['role']=='admin' and target_row['active'] and db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]<=1:
                    self.send_json({'error':'آخرین مدیر فعال را نمی‌توان غیرفعال کرد'},400); return
                db.execute('UPDATE users SET active=0 WHERE id=?',(target,)); db.execute('UPDATE students SET active=0 WHERE user_id=?',(target,)); db.execute('UPDATE teachers SET active=0 WHERE user_id=?',(target,)); db.execute('DELETE FROM sessions WHERE user_id=?',(target,))
                db.execute('INSERT INTO audit_logs(user_id,action,detail) VALUES(?,?,?)',(u['id'],'deactivate_user',str(target)))
            self.send_json({'success':True}); return
        if len(parts)==3 and parts[0]=='api' and parts[1]=='classes' and parts[2].isdigit():
            u=self.require_user('classManagement',csrf=True)
            if not u:return
            with connect() as db: db.execute('UPDATE classes SET active=0 WHERE id=?',(int(parts[2]),))
            self.send_json({'success':True}); return
        if len(parts)!=3 or parts[0]!='api' or parts[1] not in ('announcements','blogs') or not parts[2].isdigit(): self.send_json({'error':'مسیر پیدا نشد'},404); return
        kind='announcement' if parts[1]=='announcements' else 'blog'
        u=self.require_user(kind,csrf=True)
        if not u:return
        with connect() as db:
            db.execute(f'DELETE FROM {parts[1]} WHERE id=?',(int(parts[2]),))
            db.execute('INSERT INTO audit_logs(user_id,action,detail) VALUES(?,?,?)',(u['id'],'delete_'+kind,parts[2]))
        self.send_json({'success':True})

def main():
    init_db()
    print('سامانه محلی مدرسه آماده است: http://127.0.0.1:%s' % PORT)
    print('برای دسترسی شبکه داخلی از IP این لپ‌تاپ و همین پورت استفاده کنید.')
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
if __name__=='__main__': main()
