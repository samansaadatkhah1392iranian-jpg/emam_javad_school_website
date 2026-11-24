"""Import a JSON export from the old Cloudflare Worker /api/data into SQLite.
Run while the new local server is stopped: python legacy_import.py schoolData.json
"""
import json, sys
from pathlib import Path
from backend.db import init_db, connect
from backend.security import hash_password

ROLE_MAP={'admin':'admin','دانش‌آموز':'student','student':'student','دبیر':'teacher','teacher':'teacher','مسئول پایه':'grade_manager','معاون مالی':'financial','معاون نظامت':'disciplinary','معاون آموزشی':'educational','معاون پرورشی':'cultural','معاون پژوهشی':'research','مسئول رسانه':'media'}

def run(filename):
    data=json.loads(Path(filename).read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict): raise ValueError('فایل JSON باید یک شیء شامل schoolData باشد')
    init_db(); counts={}
    with connect() as db:
        users=data.get('users',{}) or {}; idmap={}
        for username,old in users.items():
            if not isinstance(old,dict): continue
            name=str(old.get('name') or old.get('full_name') or username)[:120]
            password=str(old.get('password') or '')
            if len(password)<10: continue
            role=ROLE_MAP.get(str(old.get('role','student')),'student')
            grade=old.get('grade'); grade=grade if grade in ('هفتم','هشتم','نهم') else None
            cls=str(old.get('class') or old.get('class_name') or '').strip() or None
            if role=='student' and (not grade or not cls): role='student'
            try:
                cur=db.execute('INSERT INTO users(username,password_hash,full_name,role,grade,class_name,must_change_password) VALUES(?,?,?,?,?,?,1)',(str(username)[:60],hash_password(password),name,role,grade,cls))
                uid=cur.lastrowid; idmap[username]=uid
                for perm in old.get('permissions',[]) or []:
                    db.execute('INSERT OR IGNORE INTO user_permissions(user_id,permission_code) VALUES(?,?)',(uid,str(perm)))
            except Exception: continue
        counts['users']=len(idmap)
        for kind,table in [('announcements','announcements'),('blogs','blogs')]:
            for x in data.get(kind,[]) or []:
                if not isinstance(x,dict) or not x.get('title') or not x.get('body'): continue
                scope=x.get('scope','school'); scope=scope if scope in ('school','grade','class') else 'school'
                grade=x.get('grade') if scope!='school' else None; cls=x.get('class_name') if scope=='class' else None
                author=idmap.get(x.get('author'))
                db.execute('INSERT INTO '+table+'(title,body,scope,grade,class_name,author_id) VALUES(?,?,?,?,?,?)',(str(x['title'])[:180],str(x['body'])[:20000],scope,grade,cls,author))
            counts[kind]=len(data.get(kind,[]) or [])
        for x in data.get('daily',[]) or []:
            if isinstance(x,dict) and x.get('title') and x.get('body'):
                db.execute('INSERT INTO daily_posts(title,body,post_date) VALUES(?,?,?)',(str(x['title'])[:180],str(x['body'])[:10000],str(x.get('date') or '')))
        counts['daily']=db.execute('SELECT COUNT(*) FROM daily_posts').fetchone()[0]
        # Old schedule values were free text by grade/day; retain the complete text in one timetable slot.
        for grade,days in (data.get('schedules',{}) or {}).items():
            if grade not in ('هفتم','هشتم','نهم') or not isinstance(days,dict): continue
            for day,text in days.items():
                if day not in ('شنبه','یکشنبه','دوشنبه','سه‌شنبه','چهارشنبه') or not str(text).strip(): continue
                db.execute('INSERT OR IGNORE INTO schedules(grade,class_name,weekday,period,subject) VALUES(?,?,?,?,?)',(grade,'عمومی',day,1,str(text)[:1000]))
        counts['schedules']=db.execute('SELECT COUNT(*) FROM schedules').fetchone()[0]
        for username,forms in (data.get('studentForms',{}) or {}).items():
            uid=idmap.get(username)
            if not uid: continue
            for f in forms or []:
                cur=db.execute('INSERT INTO student_activity_forms(student_user_id,activity_date,note) VALUES(?,?,?)',(uid,str(f.get('date') or ''),''))
                for subject in f.get('subjects',[]) or []:
                    try: hours=float(subject.get('hours',0))
                    except Exception: hours=0
                    if 0<=hours<=24: db.execute('INSERT INTO student_activity_items(form_id,subject,hours,description) VALUES(?,?,?,?)',(cur.lastrowid,str(subject.get('name') or 'درس')[:100],hours,str(subject.get('note') or '')[:1000]))
        counts['activities']=db.execute('SELECT COUNT(*) FROM student_activity_forms').fetchone()[0]
        for exam in data.get('exams',[]) or []:
            if not isinstance(exam,dict) or not exam.get('title'): continue
            grade=exam.get('grade') if exam.get('grade') in ('هفتم','هشتم','نهم') else 'هفتم'
            cur=db.execute('INSERT INTO exams(title,description,subject,grade,status) VALUES(?,?,?,?,?)',(str(exam['title'])[:180],str(exam.get('desc') or '')[:2000],'عمومی',grade,'draft'))
            for i,q in enumerate(exam.get('questions',[]) or [],1):
                if isinstance(q,dict): db.execute('INSERT INTO exam_questions(exam_id,position,points,question_text,question_type,options_json,answer) VALUES(?,?,?,?,?,?,?)',(cur.lastrowid,i,1,str(q.get('q') or q.get('question') or '')[:5000],str(q.get('type') or 'essay'),json.dumps(q.get('options',[]),ensure_ascii=False),str(q.get('answer',''))))
        counts['exams']=db.execute('SELECT COUNT(*) FROM exams').fetchone()[0]
        for x in data.get('classReports',[]) or []:
            if isinstance(x,dict):
                db.execute('INSERT INTO class_reports(grade,class_name,subject,session_date,topic,taught,homework) VALUES(?,?,?,?,?,?,?)',('هفتم',str(x.get('className') or 'عمومی')[:40],'عمومی',str(x.get('date') or ''),str(x.get('done') or 'گزارش قدیمی')[:180],str(x.get('done') or '')[:4000],str(x.get('homework') or '')[:2000]))
        counts['class_reports']=db.execute('SELECT COUNT(*) FROM class_reports').fetchone()[0]
        layout=data.get('layout',{}) or {}
        for old,new in [('welcome','welcome'),('about','description'),('address','address'),('phone','phone'),('hours','working_hours')]:
            if layout.get(old) is not None: db.execute('INSERT OR REPLACE INTO school_settings(key,value) VALUES(?,?)',(new,str(layout[old])[:4000]))
        for x in data.get('responsibilities',[]) or []:
            if isinstance(x,dict): db.execute('INSERT INTO responsibilities(student_name,grade,class_name,title,supervisor,description) VALUES(?,?,?,?,?,?)',(str(x.get('name') or '')[:120],'هفتم','عمومی',str(x.get('title') or 'مسئولیت')[:120],str(x.get('boss') or '')[:120],''))
        db.execute('INSERT INTO audit_logs(action,detail) VALUES(?,?)',('legacy_import',json.dumps(counts,ensure_ascii=False)))
    return counts

if __name__=='__main__':
    if len(sys.argv)!=2: raise SystemExit('استفاده: python legacy_import.py schoolData.json')
    print('توجه: حساب‌های واردشده باید در اولین ورود رمز خود را تغییر دهند.')
    print(json.dumps(run(sys.argv[1]),ensure_ascii=False,indent=2))
