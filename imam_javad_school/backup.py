"""Portable ZIP backup and validated restore for the local school system."""
from datetime import datetime
from pathlib import Path, PurePosixPath
import shutil, sys, zipfile, tempfile, sqlite3
from config import BASE_DIR, DB_PATH, BACKUP_DIR, UPLOAD_DIR
from backend.db import connect

BACKUP_DIR.mkdir(exist_ok=True)

def create_backup():
    name='school-backup-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.zip'
    target=BACKUP_DIR/name
    temp=BACKUP_DIR/'school-backup-temp.db'
    source=connect(); dest=sqlite3.connect(str(temp))
    try: source.backup(dest)
    finally: dest.close(); source.close()
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
        z.write(temp,'database/school.db')
        for p in UPLOAD_DIR.rglob('*'):
            if p.is_file(): z.write(p,str(p.relative_to(BASE_DIR)).replace('\\','/'))
    temp.unlink(missing_ok=True)
    with connect() as db:
        db.execute('INSERT INTO backup_records(filename,size_bytes) VALUES(?,?)',(name,target.stat().st_size))
    old=sorted(BACKUP_DIR.glob('school-backup-*.zip'),key=lambda x:x.stat().st_mtime,reverse=True)
    for stale in old[10:]: stale.unlink(missing_ok=True)
    return target

def restore_backup(archive):
    archive=Path(archive).resolve()
    if not archive.is_file() or not zipfile.is_zipfile(str(archive)): raise ValueError('فایل پشتیبان معتبر نیست')
    with zipfile.ZipFile(archive,'r') as z:
        names=z.namelist()
        if 'database/school.db' not in names: raise ValueError('پایگاه داده در فایل پشتیبان وجود ندارد')
        for name in names:
            q=PurePosixPath(name)
            if q.is_absolute() or '..' in q.parts: raise ValueError('مسیر ناامن در فایل پشتیبان')
        with tempfile.TemporaryDirectory() as td:
            tempdb=Path(td)/'school.db'
            tempdb.write_bytes(z.read('database/school.db'))
            con=sqlite3.connect(str(tempdb))
            try:
                result=con.execute('PRAGMA integrity_check').fetchone()[0]
                if result!='ok': raise ValueError('سلامت پایگاه داده تأیید نشد')
                tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'users','sessions','announcements','blogs'}.issubset(tables): raise ValueError('ساختار پایگاه داده با سامانه سازگار نیست')
            finally: con.close()
            safety=create_backup() if DB_PATH.exists() else None
            DB_PATH.parent.mkdir(parents=True,exist_ok=True)
            for suffix in ('-wal','-shm'):
                Path(str(DB_PATH)+suffix).unlink(missing_ok=True)
            shutil.copy2(tempdb,DB_PATH)
            for name in names:
                if name.startswith('uploads/') and not name.endswith('/'):
                    dest=(BASE_DIR/name).resolve()
                    if str(dest).startswith(str(UPLOAD_DIR.resolve())+str(Path('/'))):
                        dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes(z.read(name))
    return safety

if __name__=='__main__':
    if len(sys.argv)==1:
        print('نسخه پشتیبان ساخته شد:',create_backup())
    elif len(sys.argv)==3 and sys.argv[1]=='restore':
        print('هشدار: بازیابی اطلاعات فعلی را با نسخه پشتیبان جایگزین می‌کند.')
        if input('برای ادامه عبارت RESTORE را وارد کنید: ').strip()!='RESTORE': raise SystemExit('لغو شد')
        safety=restore_backup(sys.argv[2])
        print('بازیابی انجام شد. نسخه پشتیبان قبل از بازیابی:',safety or 'وجود نداشت')
    else:
        raise SystemExit('استفاده: python backup.py [restore backup.zip]')
