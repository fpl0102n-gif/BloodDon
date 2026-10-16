-- ==============================================================================
-- إعداد قاعدة بيانات PostgreSQL المُدارة للإنتاج والتجريب (GATE-G3 / D-09)
-- Managed PostgreSQL Staging & Production Initialization Script
-- Reference: docs/08-setup-env.md §14, docs/02-database.md §7, docs/13-external-tracks.md §2
-- ==============================================================================
--
-- تعليمات التشغيل في لوحة مزوّد قاعدة البيانات المُدارة (Supabase, Neon, AWS RDS, etc.):
-- 1. اتصل بـ SQL Editor كمسؤول قاعدة البيانات الأساسي (postgres أو admin).
-- 2. استبدل 'REPLACE_WITH_STRONG_MIGRATOR_PASSWORD' بكلمة مرور قوية ومولدة عشوائياً لدور app_migrator.
-- 3. استبدل 'REPLACE_WITH_STRONG_APP_USER_PASSWORD' بكلمة مرور قوية ومولدة عشوائياً لدور app_user.
-- 4. شغّل هذا السكربت كاملاً في نافذة SQL.
-- 5. ملاحظة لمستخدمي Supabase: استخدم منفذ Session Pooler (5432) وليس Transaction Pooler (6543)
--    في سلاسل الاتصال لضمان عمل هجرات Alembic والمعاملات بشكل سليم.
-- ==============================================================================

-- 1. إنشاء الأدوار بكلمات مرور الإنتاج/التجريب إن لم تكن موجودة
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'app_migrator') THEN
    CREATE ROLE app_migrator WITH LOGIN PASSWORD 'REPLACE_WITH_STRONG_MIGRATOR_PASSWORD';
  END IF;

  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'app_user') THEN
    CREATE ROLE app_user WITH LOGIN PASSWORD 'REPLACE_WITH_STRONG_APP_USER_PASSWORD';
  END IF;
END
$$;

-- 2. منح صلاحية الاتصال على قاعدة البيانات الحالية
DO $$
BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO app_migrator, app_user', current_database());
END
$$;

-- 3. ضبط صلاحيات المخطط العام (public)
-- app_migrator: يملك صلاحيات DDL كاملة لإنشاء وتعديل الجداول وتطبيق هجرات Alembic
GRANT ALL ON SCHEMA public TO app_migrator;

-- app_user: يملك صلاحية الاستخدام فقط (USAGE) دون إمكانية إنشاء جداول (DDL)
GRANT USAGE ON SCHEMA public TO app_user;

-- 4. إتاحة ضبط الصلاحيات الافتراضية
-- في PostgreSQL 14+، يلزم منح دور app_migrator للمستخدم الحالي ليتمكن من ضبط DEFAULT PRIVILEGES له
DO $$
BEGIN
  EXECUTE format('GRANT app_migrator TO %I', current_user);
EXCEPTION
  WHEN OTHERS THEN
    NULL; -- تجاهل الخطأ في بيئات لا تدعم التوريث المباشر
END
$$;

-- 5. الصلاحيات الافتراضية للجداول والمتسلسلات المستقبلية التي ينشئها app_migrator
ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user;

ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO app_user;

-- 6. استعلام التحقق السريع من نجاح التهيئة
SELECT
    rolname AS role_name,
    rolcanlogin AS can_login,
    rolsuper AS is_superuser
FROM pg_catalog.pg_roles
WHERE rolname IN ('app_migrator', 'app_user')
ORDER BY rolname;
