import getpass
from backend.db import init_db
from backend.auth import create_initial_admin
init_db()
print('ساخت مدیر اولیه سامانه')
username=input('نام کاربری مدیر: ').strip()
name=input('نام نمایشی مدیر: ').strip() or 'مدیر سامانه'
password=getpass.getpass('رمز عبور (حداقل ۱۰ نویسه): ')
confirm=getpass.getpass('تکرار رمز عبور: ')
if len(username)<3 or len(password)<10 or password!=confirm:
    raise SystemExit('اطلاعات نامعتبر است؛ نام کاربری حداقل ۳ و رمز حداقل ۱۰ نویسه باشد و دو رمز یکسان باشند.')
create_initial_admin(username,password,name)
print('حساب مدیر ساخته شد. رمز را در اختیار دیگران قرار ندهید.')
