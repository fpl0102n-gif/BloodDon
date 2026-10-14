-- ==============================================================================
-- إعداد أدوار قاعدة البيانات للتطوير والاختبار (Database Roles Setup)
-- Mapped to: docs/02-database.md §7 & docs/08-setup-env.md §6.1
-- ==============================================================================

-- 1. إنشاء الأدوار بكلمات مرور التطوير إن لم تكن موجودة
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'app_migrator') THEN
    CREATE ROLE app_migrator LOGIN PASSWORD 'app_migrator_dev_password';
  END IF;

  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'app_user') THEN
    CREATE ROLE app_user LOGIN PASSWORD 'app_user_dev_password';
  END IF;
END
$$;

-- 2. منح صلاحية الاتصال على قاعدة البيانات الحالية (يعمل تلقائيًا مع bloodday و bloodday_test)
DO $$
BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO app_migrator, app_user', current_database());
END
$$;

-- 3. صلاحيات المخطط (public)
-- app_migrator: يملك صلاحيات DDL كاملة لتشغيل هجرات Alembic
GRANT ALL ON SCHEMA public TO app_migrator;

-- app_user: صلاحيات الاستخدام بدون صلاحيات DDL
GRANT USAGE ON SCHEMA public TO app_user;

-- 4. الصلاحيات الافتراضية للجداول والمتسلسلات التي ينشئها app_migrator
ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user;

ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO app_user;
