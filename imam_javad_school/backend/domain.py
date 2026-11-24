"""Structured school-domain API helpers. Uses only Python standard library and SQLite."""
import csv, io, json, math, re
from datetime import datetime
from backend.db import connect

GRADES = ('هفتم','هشتم','نهم')
WEEKDAYS = ('شنبه','یکشنبه','دوشنبه','سه‌شنبه','چهارشنبه')
RESOURCES = {
 'students': ('students','classManagement',('student_no','first_name','last_name','grade','class_name','guardian_name','guardian_phone','active'),('first_name','last_name','grade','class_name')),
 'teachers': ('teachers','manageAccounts',('full_name','subject','phone','active','user_id'),('full_name',)),
 'schedules': ('schedules','schedule',('grade','class_name','weekday','period','start_time','end_time','subject','teacher','room'),('grade','class_name','weekday','period','subject')),
 'daily': ('daily_posts','daily',('title','body','post_date'),('title','body','post_date')),
 'questions': ('question_bank','questionBank',('title','body','subject','grade','topic','difficulty','question_type','options_json','answer','explanation'),('title','body','subject','grade','question_type')),
 'exams': ('exams','exam',('title','description','subject','grade','class_name','starts_at','ends_at','duration_minutes','total_score','negative_score','status','reveal_result'),('title','subject','grade')),
 'class-reports': ('class_reports','classReport',('grade','class_name','subject','session_date','topic','taught','homework','notes'),('grade','class_name','subject','session_date','topic')),
 'responsibilities': ('responsibilities','responsibility',('student_user_id','student_name','grade','class_name','title','description','supervisor','start_date','end_date'),('student_name','grade','class_name','title')),
 'financial': ('financial_records','financialManagement',('student_user_id','title','amount','due_date','paid_date','status','notes'),('title','amount')),
 'disciplinary': ('disciplinary_records','disciplinaryManagement',('student_user_id','grade','class_name','incident_date','category','description','action_taken'),('incident_date','category','description')),
}

def clean_payload(resource, data):
    table, perm, fields, required = RESOURCES[resource]
    if not isinstance(data, dict): raise ValueError('ساختار اطلاعات نامعتبر است')
    out={k:data[k] for k in fields if k in data}
    for k in required:
        if out.get(k) is None or str(out.get(k)).strip()=='': raise ValueError('فیلد الزامی خالی است: '+k)
    for k in ('grade',):
        if k in out and out[k] not in GRADES: raise ValueError('پایه نامعتبر است')
    if resource=='schedules':
        if out['weekday'] not in WEEKDAYS: raise ValueError('روز هفته نامعتبر است')
        out['period']=int(out['period'])
        if not 1 <= out['period'] <= 12: raise ValueError('شماره زنگ نامعتبر است')
    if resource=='questions':
        if out.get('difficulty','متوسط') not in ('آسان','متوسط','دشوار'): raise ValueError('سطح دشواری نامعتبر است')
        if out.get('options_json'):
            parsed=json.loads(out['options_json'])
            if not isinstance(parsed,list): raise ValueError('گزینه‌ها باید فهرست باشند')
    if resource=='exams':
        out['duration_minutes']=int(out.get('duration_minutes') or 30)
        out['total_score']=float(out.get('total_score') or 20)
        out['negative_score']=float(out.get('negative_score') or 0)
        if not 1<=out['duration_minutes']<=600 or not 0<out['total_score']<=1000 or out['negative_score']<0: raise ValueError('مدت یا نمره آزمون نامعتبر است')
        if out.get('status','draft') not in ('draft','published','closed'): raise ValueError('وضعیت آزمون نامعتبر است')
    if resource=='responsibilities' and out.get('student_user_id'):
        out['student_user_id']=int(out['student_user_id'])
    if resource=='students':
        if out.get('active') is not None: out['active']=1 if str(out['active']).lower() not in ('0','false','no') else 0
    return out

def list_resource(resource, user, query=''):
    table, perm, fields, required=RESOURCES[resource]
    with connect() as db:
        if resource=='students' and user['role']=='student':
            return []
        if resource=='financial' and user['role']=='student':
            rows=db.execute('SELECT * FROM financial_records WHERE student_user_id=? ORDER BY id DESC',(user['id'],)).fetchall()
        elif resource=='disciplinary' and user['role']=='student':
            rows=db.execute('SELECT * FROM disciplinary_records WHERE student_user_id=? ORDER BY id DESC',(user['id'],)).fetchall()
        elif resource=='responsibilities' and user['role']=='student':
            rows=db.execute('SELECT * FROM responsibilities WHERE student_user_id=? OR (grade=? AND class_name=?) ORDER BY id DESC',(user['id'],user.get('grade'),user.get('class_name'))).fetchall()
        elif resource=='class-reports' and user['role']=='student':
            rows=db.execute('SELECT * FROM class_reports WHERE grade=? AND class_name=? ORDER BY session_date DESC',(user.get('grade'),user.get('class_name'))).fetchall()
        elif resource=='schedules' and user['role']=='student':
            rows=db.execute('SELECT * FROM schedules WHERE grade=? AND class_name=? ORDER BY weekday,period',(user.get('grade'),user.get('class_name'))).fetchall()
        elif resource=='exams' and user['role']=='student':
            rows=db.execute("SELECT id,title,description,subject,grade,class_name,starts_at,ends_at,duration_minutes,total_score,status,created_at FROM exams WHERE grade=? AND (class_name IS NULL OR class_name='' OR class_name=?) AND status='published' ORDER BY id DESC",(user.get('grade'),user.get('class_name'))).fetchall()
        else:
            rows=db.execute('SELECT * FROM '+table+' ORDER BY id DESC LIMIT 500').fetchall()
    result=[dict(r) for r in rows]
    if query:
        q=query.casefold(); result=[r for r in result if any(q in str(v).casefold() for v in r.values() if v is not None)]
    return result

def create_resource(resource, data, user):
    table, perm, fields, required=RESOURCES[resource]
    d=clean_payload(resource,data)
    if resource=='daily': d['author_id']=user['id']
    if resource=='exams': d['teacher_id']=user['id']
    if resource=='questions': d['author_id']=user['id']
    if resource=='class-reports': d['teacher_id']=user['id']
    if not d: raise ValueError('اطلاعاتی برای ذخیره وجود ندارد')
    cols=list(d)
    with connect() as db:
        cur=db.execute('INSERT INTO '+table+' ('+','.join(cols)+') VALUES ('+','.join('?' for _ in cols)+')',[d[x] for x in cols])
        rid=cur.lastrowid
        db.execute('INSERT INTO audit_logs(user_id,action,detail) VALUES(?,?,?)',(user['id'],'create_'+resource,str(rid)))
    return rid

def update_resource(resource, rid, data, user):
    table, perm, fields, required=RESOURCES[resource]
    d=clean_payload(resource,data)
    if not d: raise ValueError('اطلاعاتی برای ویرایش وجود ندارد')
    cols=list(d)
    with connect() as db:
        cur=db.execute('UPDATE '+table+' SET '+','.join(k+'=?' for k in cols)+' WHERE id=?',[d[k] for k in cols]+[rid])
        if not cur.rowcount: return False
        db.execute('INSERT INTO audit_logs(user_id,action,detail) VALUES(?,?,?)',(user['id'],'update_'+resource,str(rid)))
    return True

def delete_resource(resource, rid, user):
    table, perm, fields, required=RESOURCES[resource]
    with connect() as db:
        cur=db.execute('DELETE FROM '+table+' WHERE id=?',(rid,))
        if cur.rowcount: db.execute('INSERT INTO audit_logs(user_id,action,detail) VALUES(?,?,?)',(user['id'],'delete_'+resource,str(rid)))
        return bool(cur.rowcount)

def save_activity(data, user):
    date=str(data.get('activity_date','')).strip(); items=data.get('items',[])
    if not date or not isinstance(items,list) or not items or len(items)>20: raise ValueError('تاریخ و حداقل یک ردیف درس لازم است')
    owner=int(data.get('student_user_id') or user['id'])
    if user['role']=='student' and owner!=user['id']: raise PermissionError('فقط فرم فعالیت خودتان را می‌توانید ثبت کنید')
    clean=[]; total=0
    for x in items:
        subject=str(x.get('subject','')).strip(); hours=float(x.get('hours',0))
        if not subject or not math.isfinite(hours) or not 0<=hours<=24: raise ValueError('نام درس یا ساعت مطالعه نامعتبر است')
        total+=hours; clean.append((subject,hours,str(x.get('description',''))[:1000]))
    if total>24: raise ValueError('مجموع ساعت روز نمی‌تواند بیشتر از ۲۴ باشد')
    with connect() as db:
        cur=db.execute('INSERT INTO student_activity_forms(student_user_id,activity_date,note) VALUES(?,?,?)',(owner,date,str(data.get('note',''))[:2000]))
        fid=cur.lastrowid
        db.executemany('INSERT INTO student_activity_items(form_id,subject,hours,description) VALUES(?,?,?,?)',[(fid,)+x for x in clean])
        db.execute('INSERT INTO audit_logs(user_id,action,detail) VALUES(?,?,?)',(user['id'],'create_activity',str(fid)))
    return fid

def get_activities(user):
    with connect() as db:
        if user['role']=='student':
            forms=db.execute('SELECT * FROM student_activity_forms WHERE student_user_id=? ORDER BY activity_date DESC LIMIT 180',(user['id'],)).fetchall()
        elif 'studentForm' in user['permissions'] or user['role']=='admin':
            forms=db.execute('SELECT f.*,u.full_name student_name FROM student_activity_forms f JOIN users u ON u.id=f.student_user_id ORDER BY f.activity_date DESC LIMIT 500').fetchall()
        else: raise PermissionError('مجوز مشاهده فرم فعالیت ندارید')
        result=[]
        for f in forms:
            x=dict(f); x['items']=[dict(r) for r in db.execute('SELECT subject,hours,description FROM student_activity_items WHERE form_id=?',(f['id'],))]; x['total_hours']=sum(float(i['hours']) for i in x['items']); result.append(x)
        return result

def save_report_card(data,user):
    student_id=int(data.get('student_user_id') or (user['id'] if user['role']=='student' else 0))
    if user['role']=='student' and student_id!=user['id']: raise PermissionError('دسترسی به کارنامه دیگران مجاز نیست')
    if not student_id: raise ValueError('دانش‌آموز انتخاب نشده است')
    subjects=data.get('subjects',[])
    if not isinstance(subjects,list) or not subjects or len(subjects)>30: raise ValueError('حداقل یک درس و حداکثر ۳۰ درس لازم است')
    if str(data.get('status','draft')) not in ('draft','published'): raise ValueError('وضعیت کارنامه نامعتبر است')
    if not str(data.get('school_year','')).strip() or not str(data.get('term','')).strip(): raise ValueError('سال تحصیلی و نوبت الزامی است')
    for x in subjects:
        a=float(x.get('continuous_score',-1)); b=float(x.get('final_score',-1)); c=float(x.get('coefficient',1))
        if not (0<=a<=20 and 0<=b<=20 and 0<c<=20): raise ValueError('نمره یا ضریب خارج از محدوده است')
    with connect() as db:
        u=db.execute('SELECT grade,class_name FROM users WHERE id=?',(student_id,)).fetchone()
        if not u: raise ValueError('دانش‌آموز پیدا نشد')
        cur=db.execute('INSERT INTO report_cards(student_user_id,grade,class_name,school_year,term,status) VALUES(?,?,?,?,?,?)',(student_id,u['grade'] or '',u['class_name'] or '',str(data.get('school_year','')),str(data.get('term','')),str(data.get('status','draft'))))
        cid=cur.lastrowid
        for x in subjects: db.execute('INSERT INTO report_card_subjects(report_card_id,subject,continuous_score,final_score,coefficient) VALUES(?,?,?,?,?)',(cid,str(x['subject'])[:100],float(x['continuous_score']),float(x['final_score']),float(x.get('coefficient',1))))
        db.execute('INSERT INTO audit_logs(user_id,action,detail) VALUES(?,?,?)',(user['id'],'create_report_card',str(cid)))
    return cid

def get_report_cards(user):
    with connect() as db:
        if user['role']=='student': rows=db.execute('SELECT * FROM report_cards WHERE student_user_id=? AND status="published" ORDER BY id DESC',(user['id'],)).fetchall()
        else: rows=db.execute('SELECT r.*,u.full_name student_name FROM report_cards r JOIN users u ON u.id=r.student_user_id ORDER BY r.id DESC LIMIT 500').fetchall()
        out=[]
        for r in rows:
            x=dict(r); x['subjects']=[dict(a) for a in db.execute('SELECT subject,continuous_score,final_score,coefficient,(continuous_score+final_score)/2 AS average FROM report_card_subjects WHERE report_card_id=?',(r['id'],))]
            den=sum(a['coefficient'] for a in x['subjects']) or 1
            x['average']=sum(a['average']*a['coefficient'] for a in x['subjects'])/den
            x['continuous_average']=sum(a['continuous_score'] for a in x['subjects'])/max(1,len(x['subjects']))
            x['final_average']=sum(a['final_score'] for a in x['subjects'])/max(1,len(x['subjects']))
            out.append(x)
        return out

def import_users_csv(csv_text, user, commit=True):
    if len(csv_text)>1_000_000: raise ValueError('فایل بیش از حد بزرگ است')
    reader=csv.DictReader(io.StringIO(csv_text.lstrip('\ufeff')))
    required={'username','full_name','password','role'}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)): raise ValueError('ستون‌های لازم: username,full_name,password,role')
    from backend.security import hash_password
    created=[]; errors=[]; seen=set()
    with connect() as db:
        for n,row in enumerate(reader, start=2):
            try:
                username=(row.get('username') or '').strip(); full=(row.get('full_name') or '').strip(); password=row.get('password') or ''; role=(row.get('role') or 'student').strip()
                grade=(row.get('grade') or '').strip() or None; cls=(row.get('class_name') or '').strip() or None
                if len(username)<3 or len(password)<10 or not full or role not in ('student','teacher','grade_manager','financial','disciplinary','educational','cultural','research','media'): raise ValueError('اطلاعات پایه نامعتبر است')
                if role=='student' and (grade not in GRADES or not cls): raise ValueError('پایه و کلاس دانش‌آموز لازم است')
                if db.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone(): raise ValueError('نام کاربری تکراری است')
                if username in seen: raise ValueError('نام کاربری تکراری در فایل است')
                seen.add(username)
                if commit:
                    cur=db.execute('INSERT INTO users(username,password_hash,full_name,role,grade,class_name,must_change_password) VALUES(?,?,?,?,?,?,1)',(username,hash_password(password),full,role,grade,cls))
                    uid=cur.lastrowid
                    if role=='student':
                        parts=full.split(' ',1)
                        db.execute('INSERT INTO students(user_id,first_name,last_name,grade,class_name) VALUES(?,?,?,?,?)',(uid,parts[0],parts[1] if len(parts)>1 else '',grade,cls))
                    elif role=='teacher':
                        db.execute('INSERT INTO teachers(user_id,full_name) VALUES(?,?)',(uid,full))
                    created.append(uid)
                else:
                    created.append({'row':n,'username':username,'full_name':full,'role':role})
            except Exception as e: errors.append({'row':n,'error':str(e)})
        if commit: db.execute('INSERT INTO audit_logs(user_id,action,detail) VALUES(?,?,?)',(user['id'],'csv_user_import','created='+str(len(created))))
    return {'created':len(created),'valid_rows':created if not commit else [],'errors':errors,'committed':bool(commit)}


def import_users_xlsx_b64(encoded, user, commit=True):
    import base64, zipfile, xml.etree.ElementTree as ET
    raw=base64.b64decode(encoded.encode('ascii'),validate=True)
    if len(raw)>2_000_000: raise ValueError('فایل Excel بیش از حد بزرگ است')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        ns={'a':'http://schemas.openxmlformats.org/spreadsheetml/2006/main','r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
        shared=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            root=ET.fromstring(z.read('xl/sharedStrings.xml'))
            for si in root.findall('a:si',ns): shared.append(''.join(t.text or '' for t in si.findall('.//a:t',ns)))
        sheet='xl/worksheets/sheet1.xml'
        if sheet not in z.namelist(): raise ValueError('برگه اول فایل پیدا نشد')
        root=ET.fromstring(z.read(sheet)); rows=[]
        for row in root.findall('.//a:sheetData/a:row',ns):
            vals={}
            for cell in row.findall('a:c',ns):
                ref=cell.attrib.get('r',''); col=''.join(ch for ch in ref if ch.isalpha()); v=cell.find('a:v',ns); val=v.text if v is not None else ''
                if cell.attrib.get('t')=='s' and val: val=shared[int(val)]
                elif cell.attrib.get('t')=='inlineStr':
                    inline=cell.find('a:is',ns); val=''.join(t.text or '' for t in inline.findall('.//a:t',ns)) if inline is not None else ''
                vals[col]=val
            rows.append(vals)
        if not rows: raise ValueError('فایل خالی است')
        def colnum(label):
            n=0
            for c in label: n=n*26+ord(c.upper())-64
            return n
        maxcol=max((colnum(k) for r in rows for k in r),default=0)
        matrix=[[r.get(_colname(i),'') for i in range(1,maxcol+1)] for r in rows]
        headers=[str(x).strip() for x in matrix[0]]
        buf=io.StringIO(); writer=csv.DictWriter(buf,fieldnames=headers); writer.writeheader()
        for row in matrix[1:]: writer.writerow({headers[i]:row[i] if i<len(row) else '' for i in range(len(headers))})
        return import_users_csv(buf.getvalue(),user,commit)

def _colname(n):
    out=''
    while n:
        n,rem=divmod(n-1,26); out=chr(65+rem)+out
    return out

def update_activity(form_id, data, user):
    date=str(data.get('activity_date','')).strip(); items=data.get('items',[])
    if not date or not isinstance(items,list) or not items or len(items)>20: raise ValueError('تاریخ و حداقل یک ردیف درس لازم است')
    clean=[]; total=0.0
    for item in items:
        subject=str(item.get('subject','')).strip(); hours=float(item.get('hours',0))
        if not subject or not math.isfinite(hours) or not 0<=hours<=24: raise ValueError('نام درس یا ساعت مطالعه نامعتبر است')
        total+=hours; clean.append((subject,hours,str(item.get('description',''))[:1000]))
    if total>24: raise ValueError('مجموع ساعت روز نمی‌تواند بیشتر از ۲۴ باشد')
    with connect() as db:
        row=db.execute('SELECT student_user_id FROM student_activity_forms WHERE id=?',(form_id,)).fetchone()
        if not row: return False
        if user['role']=='student' and row['student_user_id']!=user['id']: raise PermissionError('فقط فرم فعالیت خودتان را می‌توانید ویرایش کنید')
        if user['role']!='student' and not ('studentForm' in user['permissions'] or user['role']=='admin'): raise PermissionError('مجوز ویرایش فرم فعالیت ندارید')
        db.execute('UPDATE student_activity_forms SET activity_date=?,note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(date,str(data.get('note',''))[:2000],form_id))
        db.execute('DELETE FROM student_activity_items WHERE form_id=?',(form_id,))
        db.executemany('INSERT INTO student_activity_items(form_id,subject,hours,description) VALUES(?,?,?,?)',[(form_id,)+x for x in clean])
        db.execute('INSERT INTO audit_logs(user_id,action,detail) VALUES(?,?,?)',(user['id'],'update_activity',str(form_id)))
    return True

def delete_activity(form_id, user):
    with connect() as db:
        row=db.execute('SELECT student_user_id FROM student_activity_forms WHERE id=?',(form_id,)).fetchone()
        if not row: return False
        if user['role']=='student' and row['student_user_id']!=user['id']: raise PermissionError('فقط فرم فعالیت خودتان را می‌توانید حذف کنید')
        if user['role']!='student' and not ('studentForm' in user['permissions'] or user['role']=='admin'): raise PermissionError('مجوز حذف فرم فعالیت ندارید')
        db.execute('DELETE FROM student_activity_forms WHERE id=?',(form_id,))
        db.execute('INSERT INTO audit_logs(user_id,action,detail) VALUES(?,?,?)',(user['id'],'delete_activity',str(form_id)))
    return True
