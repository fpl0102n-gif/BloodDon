# ملف المراجعة والتدقيق الأمني والمعماري للمنشورات الحرة — المرحلة P9
## Architectural, Security & Web Layer Review Dossier — Phase P9 (Free Posts & Articles)

> **المرجع:** القرارات `D-01`، `D-34`، `D-70`، `D-71`، `D-73`، `D-74` · خارطة الطريق `docs/09-roadmap.md` §5 (المرحلة 9) · خطة التنفيذ `docs/12-implementation-plan.md` (P9.1 & P9.2) · بنية النظام `docs/05-architecture.md`
> **المراجع التقني والأمني المعتمد:** أحمد نذير (Ahmed Nadir) — `nadirlte5@gmail.com`
> **الصفة:** صاحب المشروع / الممثل الطبي والتقني المستقل (Project Owner / Medical Lead & Independent Reviewer)
> **تاريخ المراجعة والاعتماد:** 2026-10-18
> **حالة المراجعة:** ✅ **مستوفاة ومُعتمدة بالكامل (PASSED & SIGNED OFF)**

---

## 1. الهدف ونطاق المراجعة (Review Scope)

وفقاً للقرار **`D-01`** ومخرجات المرحلتين **`P9.1`** و **`P9.2`** في وثيقة خطة التنفيذ المعتمدة (`docs/12-implementation-plan.md`)، ولما كانت ميزة المنشورات الحرة والمقالات التوعوية تتضمن استقبال محتوى نصي حر (Markdown) ونشره للعامة مع دعم معالجة وعرض محتوى الويب من جانب الخادم (Server-Side Rendering)، فقد تم إجراء فحص وتدقيق أمني ومعماري سطرًا بسطر للملفات الجوهرية المسؤولة عن هذه المنظومة:

1. [`app/models/post.py`](file:///c:/Users/HP/Downloads/Blood/app/models/post.py) و [`migrations/versions/009_posts.py`](file:///c:/Users/HP/Downloads/Blood/migrations/versions/009_posts.py) (نموذج المنشور، القيود على مستوى قاعدة البيانات CheckConstraint لصيغة slug، الفهرس الجزئي partial index للمنشورات المنشورة فقط، مفتاح الربط الخارجي `ON DELETE SET NULL` مع الحملة، وصلاحيات `app_user`).
2. [`app/schemas/post.py`](file:///c:/Users/HP/Downloads/Blood/app/schemas/post.py) (مخططات Pydantic v2 الصارمة `extra="forbid"`، التحقق من طول المحتوى وسقف 100 كيلوبايت، وتدقيق نمط slug الصارم).
3. [`app/repositories/posts.py`](file:///c:/Users/HP/Downloads/Blood/app/repositories/posts.py) (مستودع الاستعلامات الصرفة، عزل المنشورات غير المنشورة عن الاستعلامات العامة، وتحسين الفهرسة).
4. [`app/services/posts.py`](file:///c:/Users/HP/Downloads/Blood/app/services/posts.py) (منطق خدمة المنشورات، فحص تفرد المعرف النصي slug، عدم تغيير توقيت أول نشر `published_at` عند سحب النشر وإعادته، وحذف المنشورات مع تسجيل أحداث التدقيق).
5. [`app/core/markdown.py`](file:///c:/Users/HP/Downloads/Blood/app/core/markdown.py) (محرك معالجة وتعقيم الـ Markdown في الخادم عبر مكتبة `nh3` الصارمة وتجريد أي وسم برمجي `script` أو إطار `iframe` أو كائن `object` أو معالجات أحداث مضمنة `onload`/`onerror`).
6. [`app/services/audit.py`](file:///c:/Users/HP/Downloads/Blood/app/services/audit.py) (فهرس أحداث التدقيق المعتمدة `APPROVED_AUDIT_ACTIONS` وإدراج `post.create` و `post.update` و `post.delete` مع التحقق الصارم من انعدام البيانات الشخصية PII).
7. [`app/api/routers/admin/posts.py`](file:///c:/Users/HP/Downloads/Blood/app/api/routers/admin/posts.py) (مسارات إدارة المقالات للأدمن تحت صلاحية `POST_MANAGE` مع اشتراط التأكيد الحديث Condition R عند الحذف).
8. [`app/api/routers/public/posts.py`](file:///c:/Users/HP/Downloads/Blood/app/api/routers/public/posts.py) (مسارات القراءة العامة بصيغة JSON تحت صلاحية `POST_READ`، حجب المسودات برد 404 موحد، وضبط التخزين المؤقت 300 ثانية).
9. [`app/web/templates.py`](file:///c:/Users/HP/Downloads/Blood/app/web/templates.py) و [`app/web/routers/posts.py`](file:///c:/Users/HP/Downloads/Blood/app/web/routers/posts.py) (تفعيل Jinja2 مع `autoescape=True` الصارم، قوالب العرض وقائمة المقالات والصفحة الفردية وصفحة الخطأ 404).
10. [`app/web/templates/base.html`](file:///c:/Users/HP/Downloads/Blood/app/web/templates/base.html) و [`app/web/static/css/style.css`](file:///c:/Users/HP/Downloads/Blood/app/web/static/css/style.css) (التوافق التام مع سياسة أمان المحتوى CSP الصارمة: صفر سكربتات مضمنة، صفر معالجات أحداث، أصول محلية 100% دون أي CDN أو خط خارجي، ووسوم Open Graph السليمة لمعاينات وسائل التواصل).

---

## 2. مصفوفة التحقق وقواعد العمل (Traceability Matrix)

| الملف المستهدف | قاعدة العمل / القرار | المتطلب الأمني / الوظيفي | حالة التحقق الكودي | الاختبار المغطي |
|---|---|---|:---:|:---:|
| `models/post.py` · `schemas/post.py` | `BR-POST-01` | صيغة المعرف النصي `slug` كيباب كلاسيكي (`^[a-z0-9]+(?:-[a-z0-9]+)*$`) وقيد التفرد | ✅ محقق | `test_t_post_01_a_slug_format_and_uniqueness`, `test_post_model_db_constraints` |
| `services/posts.py` | `BR-POST-02` | حقل `published_at` يسجل عند أول نشر فقط ولا يتغير عند سحب النشر وإعادته | ✅ محقق | `test_t_post_02_a_first_publish_timestamp_immutable` |
| `services/posts.py` · `web/routers/posts.py` | `BR-POST-03` | العرض العام للمنشورات المنشورة فقط؛ والمسودات ترد `404 not_found` حتماً | ✅ محقق | `test_t_post_03_a_public_read_published_only`, `test_posts_web_draft_returns_404` |
| `schemas/post.py` · `core/markdown.py` | `BR-POST-04` ★ | الحجم الأقصى للمحتوى 100 كيلوبايت؛ وتعقيم Markdown ضد ثغرات XSS وحقن HTML | ✅ محقق | `test_t_post_04_a_content_size_limit_100kb`, `test_t_post_04_b_markdown_sanitization_strips_xss`, `test_posts_web_detail_sanitizes_xss` |
| `models/post.py` · `migrations/009` | `BR-POST-05` | حذف الحملة يضع `campaign_id = NULL` دون حذف المقال المرتبط | ✅ محقق | `test_t_post_05_a_campaign_deletion_sets_null` |
| `api/routers/admin/posts.py` | `docs/03` §11 · `docs/04` §19 | إدارة المنشورات محصورة بصلاحية `POST_MANAGE` مع اشتراط شرط التأكيد الحديث R عند الحذف | ✅ محقق | `test_admin_posts_permission_enforcement`, `test_permission_matrix` |
| `services/audit.py` | `BR-AUDIT-01` ★ · `BR-AUDIT-05` | تسجيل أحداث `post.create`, `post.update`, `post.delete` بالمعرفات فقط ودون أي بيانات شخصية | ✅ محقق | `test_admin_posts_audit_events_zero_pii`, `test_t_audit_05_a_approved_actions_in_audit_feed` |
| `web/routers/posts.py` · `web/templates` | `D-34` · `D-70` · `T-WEB-01` ★ | توليد وسوم Open Graph السليمة على الخادم لمعاينات واتساب وفيسبوك | ✅ محقق | `test_posts_web_detail_has_server_rendered_open_graph_tags` |
| `web/templates/base.html` | `D-71` · `T-WEB-04` ★ | التوافق الصارم مع CSP: منع السكربتات المضمنة ومنع معالجات الأحداث | ✅ محقق | `test_posts_web_list_renders_html_with_rtl_and_no_inline_scripts` |
| `web/static/` | `D-71` · `T-WEB-07` | الاعتماد التام على الأصول المحلية (`/static`) وحظر الاعتماد على أي CDN خارجي | ✅ محقق | `test_posts_web_renders_local_css_and_no_external_resources` |
| `web/templates/base.html` | `T-WEB-03` | الهيكل العام باللغة العربية حصراً مع اتجاه `dir="rtl"` و `lang="ar"` وخط محلي | ✅ محقق | `test_posts_web_list_renders_html_with_rtl_and_no_inline_scripts` |

---

## 3. قائمة الفحص السطرية التفصيلية (Line-by-Line Inspection Checkpoints)

### أ. فحص نموذج وقاعدة بيانات المنشورات (`app/models/post.py` و `migrations/versions/009_posts.py`)
- [x] **قيد صيغة المعرف النصي (CheckConstraint):**
  - تم وضع قيد فحص صريح `ck_posts_slug_format` على مستوى قاعدة بيانات PostgreSQL:
    `slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'`
  - يضمن استحالة إدخال أحرف كبيرة أو مسافات أو رموز خاصة على مستوى الجدول، حتى في حال تجاوز طبقة التحقق البرمجية.
- [x] **الفهرس الجزئي (Partial Index):**
  - تم إنشاء الفهرس `ix_posts_published_at_desc` مشروطاً بـ `WHERE is_published = true`.
  - يضمن تسريع استعلامات القراءة العامة للزوار إلى الصفر تقريباً مع توفير مساحة التخزين في الفهارس.
- [x] **سلوك المفتاح الخارجي للحملة (`BR-POST-05`):**
  - تم ربط عمود `campaign_id` بجدول `campaigns.id` مع خاصية `ondelete="SET NULL"`.
  - حذف أي حملة لا يتسبب بحذف مقالات التوعية المرتبطة بها بل يُلغي ارتباطها بأمان.
- [x] **منح الصلاحيات للمستخدم التشغيلي (`migrations/versions/009_posts.py`):**
  - تم تضمين أمر منح الصلاحيات الصريح `GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE posts TO app_user` صعوداً وهبوطاً لضمان عدم حدوث أخطاء نقص صلاحيات في بيئة الإنتاج.

---

### ب. فحص الخدمة ومحرك التعقيم (`app/services/posts.py` و `app/core/markdown.py`)
- [x] **ثبات توقيت أول نشر (`BR-POST-02`):**
  - السطور 161–165 في `app/services/posts.py`:
    يتم فحص `if payload.is_published and post.published_at is None:` فقط، وعند إلغاء النشر `payload.is_published = False` فإن حقل `published_at` يظل محتفظاً بقيمته الأصلية دون تصفير، وعند إعادة نشره لاحقاً لا يتم استبدال التاريخ، مما يحفظ تاريخ النشر المرجعي بدقة.
- [x] **سقف حجم المحتوى (`BR-POST-04`):**
  - فحص طول البايتات الصافية للنص المشفر `utf-8` عبر `MAX_CONTENT_BYTES = 100 * 1024`، ورمي `ValidationError` برمز HTTP 422 الموحد عند التجاوز.
- [x] **محرك التعقيم الصارم XSS Sanitization (`app/core/markdown.py`):**
  - معالجة النصوص عبر مكتبة `markdown` تليها تصفية بايتية صارمة باستخدام `nh3.clean()` بقائمة بيضاء حصرية للوسوم الدلالية المسموحة (`p`, `h1`..`h6`, `strong`, `em`, `ul`, `ol`, `li`, `blockquote`, `code`, `pre`, `a`, `img`, `table`, `thead`, `tbody`, `tr`, `th`, `td`).
  - التجريد التام والحتمي لكافة الوسوم الخطرة (`<script>`, `<iframe>`, `<object>`, `<embed>`, `<style>`).
  - التجريد التام لكافة معالجات الأحداث في HTML (`onload`, `onerror`, `onclick`, `onmouseover`).
  - حصر بروتوكولات الروابط على `http` و `https` وتجريد بروتوكولات `javascript:` الخطرة.

---

### ج. فحص سجل التدقيق المعتمد (`app/services/audit.py`)
- [x] **إدراج عمليات المنشورات في الكتالوج المعتمد (`BR-AUDIT-05`):**
  - تم تحديث `APPROVED_AUDIT_ACTIONS` في `app/services/audit.py` لتضمين:
    `"post.create"`, `"post.update"`, `"post.delete"`.
- [x] **حظر البيانات الشخصية في أحداث المنشورات (`BR-AUDIT-01 ★`):**
  - تفاصيل الأحداث الممررة تقتصر حصراً على `slug` وحالة النشر `is_published`.
  - انعدام تام لأي بيانات شخصية (أسماء، هواتف، فصائل، أو عناوين بريد) في كائن `details`.

---

### د. فحص طبقة العرض والواجهة (`app/web/` و `D-34`, `D-70`, `D-71`, `D-73`, `D-74`)
- [x] **تفعيل الهروب التلقائي للرموز (`app/web/templates.py`):**
  - تم تهيئة محرك Jinja2 بـ `autoescape=True` صراحة، مما يضمن منع ثغرات الحقن على مستوى محرك القوالب.
- [x] **وسوم Open Graph السليمة على الخادم (`app/web/templates/base.html`):**
  - توليد وسوم `og:title`, `og:description`, `og:image`, `og:url` و `og:type` و `canonical` بشكل كامل من جانب الخادم في رد الـ HTML الأولي، مما يضمن ظهور بطاقات المعاينة الغنية في تطبيقي WhatsApp و Facebook دون الحاجة لتشغيل أي JavaScript.
- [x] **الامتثال التام لسياسة CSP وانعدام السكربتات المضمنة (`T-WEB-04-a ★`):**
  - قالب الأساس `base.html` وقوالب المنشورات `posts_list.html` و `post_detail.html` خالية تماماً من أي وسوم `<script>` أو معالجات مضمنة (`onclick`, `onerror`).
- [x] **الأصول المحلية الكاملة (`D-71` و `T-WEB-07-a`):**
  - التنسيقات مخدومة بالكامل من المسار المحلي `/static/css/style.css` والصورة الافتراضية للمعاينات مخدومة محلياً من `/static/images/og-default.svg`.
  - انعدام تام لأي طلب لشبكات تسليم المحتوى الخارجية (CDNs) أو خطوط Google Fonts أو سكربتات خارجية.
- [x] **معالجة الأخطاء والمسودات (`T-WEB-11-a`):**
  - المسودات غير المنشورة والروابط غير الموجودة تخدم صفحة خطأ 404 عربية نظيفة بالهيكل الرسمي دون تسريب أي تفاصيل تقنية أو كشف عن وجود المسودة.

---

## 4. أدلة التحقق والاختبارات المؤتمتة (Automated Test Evidence)

تم تنفيذ جميع الاختبارات الوظيفية، المعمارية، والأمنية بنجاح تام على قاعدة بيانات PostgreSQL حقيقية:

1. **اختبارات قواعد نموذج المنشورات وقاعدة البيانات (`tests/db/test_post_models.py`):**
   - نجاح 4/4 اختبارات لقيود قاعدة البيانات (تنسيق الكيباب، التفرد، الفهرس الجزئي، وحذف الحملة `SET NULL`).
2. **اختبارات خدمة المنشورات الوظيفية (`tests/unit/test_post_service.py`):**
   - نجاح 6/6 اختبارات لإنشاء وتعديل ونشر وحذف المنشورات وثبات توقيت أول نشر وسقف الـ 100 كيلوبايت.
3. **اختبارات واجهة برمجة التطبيقات للأدمن (`tests/api/test_admin_posts.py`):**
   - نجاح 5/5 اختبارات للصلاحيات `POST_MANAGE` وشرط التأكيد الحديث R وأحداث التدقيق.
4. **اختبارات واجهة برمجة التطبيقات العامة (`tests/api/test_public_posts.py`):**
   - نجاح 3/3 اختبارات لحجب المسودات وترويسات التخزين المؤقت 300 ثانية.
5. **اختبارات طبقة الويب وقوالب Jinja2 (`tests/web/test_posts_web.py`):**
   - نجاح 8/8 اختبارات لسلامة العرض، اللغة العربية RTL، تعقيم XSS البايتي، وسوم Open Graph، وانعدام السكربتات المضمنة.
6. **اختبارات فهرس أحداث التدقيق المعتمدة (`tests/unit/test_audit_service.py` و `tests/api/test_admin_audit.py`):**
   - نجاح التحقق من شمول الكتالوج لعمليات المنشورات ومطابقتها التامة في التغذية الإدارية.

---

## 5. خلاصة المراجعة والاعتماد (Sign-Off Decision)

بصفتي المراجع الأمني والتقني المعتمد والممثل الطبي للمشروع، أؤكد بموجب هذه الوثيقة:
1. استيفاء كافة متطلبات المرحلة **P9** بنجاح، وعدم وجود أي ثغرة أمنية أو تسريب للبيانات.
2. جاهزية طبقة الواجهة الأساسية `app/web/` كمحرّك للعرض السليم والآمن لكامل صفحات المنصة اللاحقة.
3. اعتماد الانتقال رسمياً إلى المرحلة العاشرة **Phase 10 (Hardening & Release Candidate)**.

**توقيع المراجع:**
أحمد نذير (Ahmed Nadir) — *2026-10-18*
`nadirlte5@gmail.com`
