# Database Initialization & Roles Guide (دليل تهيئة قواعد البيانات والأدوار)

> **المرجع:** `docs/08-setup-env.md` §6 و §14، `docs/02-database.md` §7، وقرار `D-09` في `docs/11-decisions.md`.

---

## 1. الأدوار وصلاحياتها (Role Separation)

تعتمد المنصة مبدأ تقليل الصلاحيات (Principle of Least Privilege) عبر فصل العمليات إلى دورين مستقلين:

1. **`app_migrator` (دور الهجرات و DDL):**
   - يملك صلاحيات كاملة على المخطط `public`.
   - يُستخدم حصريًا لتشغيل هجرات Alembic (`uv run python -m app.cli check-migrations --upgrade`).
   - لا يُستخدم في تطبيق الويب أثناء التشغيل الاعتيادي.
   - سلسلة الاتصال: `MIGRATION_DATABASE_URL`.

2. **`app_user` (دور التشغيل الاعتيادي و DML):**
   - يملك صلاحية `USAGE` فقط على المخطط `public` (لا يملك إنشاء أو حذف جداول DDL).
   - يملك صلاحيات القراءة والكتابة الاعتيادية (`SELECT`, `INSERT`, `UPDATE`, `DELETE`) على جداول التطبيق.
   - **سجل التدقيق (`audit_log`):** محمي على مستوى قاعدة البيانات بمنع التعديل والحذف والتفريغ (`REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_log FROM app_user;`) وفقًا للقاعدة `BR-AUDIT-02`.
   - سلسلة الاتصال: `DATABASE_URL`.

---

## 2. التهيئة المحلية (Docker Compose)

في بيئة التطوير والاختبار المحلي، يتم تشغيل السكربت `scripts/db/init-roles.sql` تلقائيًا عند إقلاع حاويات PostgreSQL عبر:
```bash
docker compose up -d db db_test db_staging
```

---

## 3. التهيئة على قواعد البيانات المُدارة (Managed PostgreSQL: Supabase / Neon / AWS RDS)

عند ربط بيئة التجريب (Staging) أو الإنتاج (Production) بمزوّد سحابي مُدار (قرار `D-09` و `GATE-G3`):

1. **فتح حساب المشروع:** إنشاء قاعدة بيانات PostgreSQL 15+ على مزوّد سحابي موثوق (مثل Supabase أو Neon).
2. **تشغيل سكربت التهيئة:**
   - افتح نافذة SQL Editor في لوحة تحكم المزوّد كمسؤول (`postgres`).
   - انسخ محتوى الملف `scripts/db/init-managed-db.sql`.
   - استبدل كلمات المرور الافتراضية بكلمات مرور عشوائية قوية لكل من `app_migrator` و `app_user`.
   - نفّذ السكربت.
3. **تطبيق الهجرات:**
   ```bash
   uv run python -m app.cli check-migrations --database-url "postgresql+psycopg://app_migrator:<PASSWORD>@<HOST>:5432/<DB>?sslmode=require" --upgrade
   ```
4. **التحقق الشامل من البوابة GATE-G3:**
   ```bash
   uv run python -m app.cli verify-database \
     --migration-url "postgresql+psycopg://app_migrator:<PASSWORD>@<HOST>:5432/<DB>?sslmode=require" \
     --app-url "postgresql+psycopg://app_user:<PASSWORD>@<HOST>:5432/<DB>?sslmode=require"
   ```

### ⚠️ تنبيهات هامة لمجمّع الاتصالات (Connection Poolers):
- في **Supabase** و **PgBouncer**: يجب استخدام منفذ **Session Pooler (5432)** أو الاتصال المباشر.
- **لا تستخدم** منفذ Transaction Pooler (6543) مع هجرات Alembic أو الاستعلامات المعدّة مسبقاً (Prepared Statements) تجنباً لفشل المعاملات.
- تأكد دائمًا من ضبط `sslmode=require` في بيئات السحاب والإنتاج.
