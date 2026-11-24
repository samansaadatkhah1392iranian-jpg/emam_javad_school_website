import sqlite3
from config import DB_PATH

SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL, full_name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'student',
 grade TEXT, class_name TEXT, active INTEGER NOT NULL DEFAULT 1,
 must_change_password INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS classes (
 id INTEGER PRIMARY KEY AUTOINCREMENT, grade TEXT NOT NULL, name TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(grade,name)
);
CREATE TABLE IF NOT EXISTS permissions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, title TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_permissions (
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 permission_code TEXT NOT NULL, PRIMARY KEY(user_id, permission_code)
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 csrf_token TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS announcements (
 id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT NOT NULL,
 scope TEXT NOT NULL DEFAULT 'school', grade TEXT, class_name TEXT,
 author_id INTEGER REFERENCES users(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT
);
CREATE TABLE IF NOT EXISTS blogs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT NOT NULL,
 category TEXT, scope TEXT NOT NULL DEFAULT 'school', grade TEXT, class_name TEXT,
 author_id INTEGER REFERENCES users(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT
);
CREATE TABLE IF NOT EXISTS school_settings (
 key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT NOT NULL,
 detail TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_announcements_created ON announcements(created_at);
CREATE INDEX IF NOT EXISTS idx_blogs_created ON blogs(created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS students (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER UNIQUE REFERENCES users(id), student_no TEXT UNIQUE, first_name TEXT NOT NULL, last_name TEXT NOT NULL, grade TEXT NOT NULL, class_name TEXT NOT NULL, guardian_name TEXT, guardian_phone TEXT, active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS teachers (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER UNIQUE REFERENCES users(id), full_name TEXT NOT NULL, subject TEXT, phone TEXT, active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS schedules (id INTEGER PRIMARY KEY AUTOINCREMENT, grade TEXT NOT NULL, class_name TEXT NOT NULL, weekday TEXT NOT NULL, period INTEGER NOT NULL, start_time TEXT, end_time TEXT, subject TEXT NOT NULL, teacher TEXT, room TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(grade,class_name,weekday,period));
CREATE TABLE IF NOT EXISTS daily_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT NOT NULL, post_date TEXT NOT NULL, author_id INTEGER REFERENCES users(id), created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS student_activity_forms (id INTEGER PRIMARY KEY AUTOINCREMENT, student_user_id INTEGER NOT NULL REFERENCES users(id), activity_date TEXT NOT NULL, note TEXT, status TEXT DEFAULT 'submitted', created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT);
CREATE TABLE IF NOT EXISTS student_activity_items (id INTEGER PRIMARY KEY AUTOINCREMENT, form_id INTEGER NOT NULL REFERENCES student_activity_forms(id) ON DELETE CASCADE, subject TEXT NOT NULL, hours REAL NOT NULL CHECK(hours>=0 AND hours<=24), description TEXT);
CREATE TABLE IF NOT EXISTS question_bank (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT NOT NULL, subject TEXT NOT NULL, grade TEXT NOT NULL, topic TEXT, difficulty TEXT DEFAULT 'متوسط', question_type TEXT NOT NULL, options_json TEXT, answer TEXT, explanation TEXT, author_id INTEGER REFERENCES users(id), created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS exams (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT, subject TEXT NOT NULL, grade TEXT NOT NULL, class_name TEXT, teacher_id INTEGER REFERENCES users(id), starts_at TEXT, ends_at TEXT, duration_minutes INTEGER DEFAULT 30, total_score REAL DEFAULT 20, negative_score REAL DEFAULT 0, status TEXT DEFAULT 'draft', reveal_result INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS exam_questions (id INTEGER PRIMARY KEY AUTOINCREMENT, exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE, question_id INTEGER REFERENCES question_bank(id), position INTEGER NOT NULL, points REAL DEFAULT 1, question_text TEXT, question_type TEXT, options_json TEXT, answer TEXT, UNIQUE(exam_id,position));
CREATE TABLE IF NOT EXISTS exam_attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, exam_id INTEGER NOT NULL REFERENCES exams(id), student_user_id INTEGER NOT NULL REFERENCES users(id), started_at TEXT DEFAULT CURRENT_TIMESTAMP, submitted_at TEXT, score REAL, status TEXT DEFAULT 'in_progress', UNIQUE(exam_id,student_user_id));
CREATE TABLE IF NOT EXISTS exam_answers (id INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id INTEGER NOT NULL REFERENCES exam_attempts(id) ON DELETE CASCADE, exam_question_id INTEGER NOT NULL REFERENCES exam_questions(id), answer TEXT, is_correct INTEGER, score REAL DEFAULT 0, UNIQUE(attempt_id,exam_question_id));
CREATE TABLE IF NOT EXISTS class_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, grade TEXT NOT NULL, class_name TEXT NOT NULL, subject TEXT NOT NULL, teacher_id INTEGER REFERENCES users(id), session_date TEXT NOT NULL, topic TEXT NOT NULL, taught TEXT, homework TEXT, notes TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS report_cards (id INTEGER PRIMARY KEY AUTOINCREMENT, student_user_id INTEGER NOT NULL REFERENCES users(id), grade TEXT NOT NULL, class_name TEXT NOT NULL, school_year TEXT NOT NULL, term TEXT NOT NULL, status TEXT DEFAULT 'draft', created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(student_user_id,school_year,term));
CREATE TABLE IF NOT EXISTS report_card_subjects (id INTEGER PRIMARY KEY AUTOINCREMENT, report_card_id INTEGER NOT NULL REFERENCES report_cards(id) ON DELETE CASCADE, subject TEXT NOT NULL, continuous_score REAL NOT NULL CHECK(continuous_score BETWEEN 0 AND 20), final_score REAL NOT NULL CHECK(final_score BETWEEN 0 AND 20), coefficient REAL DEFAULT 1, UNIQUE(report_card_id,subject));
CREATE TABLE IF NOT EXISTS responsibilities (id INTEGER PRIMARY KEY AUTOINCREMENT, student_user_id INTEGER REFERENCES users(id), student_name TEXT NOT NULL, grade TEXT NOT NULL, class_name TEXT NOT NULL, title TEXT NOT NULL, description TEXT, supervisor TEXT, start_date TEXT, end_date TEXT);
CREATE TABLE IF NOT EXISTS media_files (id INTEGER PRIMARY KEY AUTOINCREMENT, original_name TEXT NOT NULL, stored_name TEXT NOT NULL UNIQUE, mime_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, uploaded_by INTEGER REFERENCES users(id), created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS backup_records (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT NOT NULL, size_bytes INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP, note TEXT);
CREATE TABLE IF NOT EXISTS failed_logins (username TEXT PRIMARY KEY, attempts INTEGER NOT NULL DEFAULT 0, locked_until TEXT);
CREATE INDEX IF NOT EXISTS idx_students_grade_class ON students(grade,class_name);
CREATE INDEX IF NOT EXISTS idx_schedules_lookup ON schedules(grade,class_name,weekday);
CREATE INDEX IF NOT EXISTS idx_activity_student_date ON student_activity_forms(student_user_id,activity_date);
CREATE INDEX IF NOT EXISTS idx_exam_grade_class ON exams(grade,class_name,status);
CREATE INDEX IF NOT EXISTS idx_reports_grade_class_date ON class_reports(grade,class_name,session_date);
CREATE TABLE IF NOT EXISTS financial_records (id INTEGER PRIMARY KEY AUTOINCREMENT, student_user_id INTEGER REFERENCES users(id), title TEXT NOT NULL, amount REAL NOT NULL CHECK(amount>=0), due_date TEXT, paid_date TEXT, status TEXT DEFAULT 'unpaid', notes TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS disciplinary_records (id INTEGER PRIMARY KEY AUTOINCREMENT, student_user_id INTEGER REFERENCES users(id), grade TEXT, class_name TEXT, incident_date TEXT NOT NULL, category TEXT NOT NULL, description TEXT NOT NULL, action_taken TEXT, author_id INTEGER REFERENCES users(id), created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS class_members (id INTEGER PRIMARY KEY AUTOINCREMENT, class_id INTEGER NOT NULL REFERENCES classes(id), user_id INTEGER NOT NULL REFERENCES users(id), school_year TEXT, UNIQUE(class_id,user_id,school_year));
CREATE TABLE IF NOT EXISTS grade_levels (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE);
INSERT OR IGNORE INTO grade_levels(name) VALUES('هفتم'),('هشتم'),('نهم');
'''

class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()

def connect():
    conn = sqlite3.connect(str(DB_PATH), timeout=15, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=15000')
    return conn

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript(SCHEMA)
        db.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)')
        db.execute('INSERT OR IGNORE INTO schema_migrations(version) VALUES(1)')
        perms = {
          'announcement':'مدیریت اطلاعیه‌ها','blog':'مدیریت بلاگ','schedule':'مدیریت برنامه درسی',
          'daily':'مدیریت برنامه روزانه','studentForm':'مشاهده فرم فعالیت','exam':'مدیریت آزمون',
          'classReport':'گزارش کلاس','reportCard':'مدیریت کارنامه','responsibility':'مسئولیت‌ها',
          'manageAccounts':'مدیریت حساب‌ها','layout':'مدیریت چیدمان','questionBank':'بانک سؤال',
          'classManagement':'مدیریت کلاس‌ها','media':'مدیریت رسانه','statistics':'گزارش‌ها','backupManagement':'پشتیبان‌گیری',
          'createAccount':'ایجاد حساب','financialManagement':'مدیریت مالی','disciplinaryManagement':'مدیریت انضباطی','educationalManagement':'مدیریت آموزشی','researchManagement':'مدیریت پژوهشی'
        }
        db.executemany('INSERT OR IGNORE INTO permissions(code,title) VALUES(?,?)', perms.items())
