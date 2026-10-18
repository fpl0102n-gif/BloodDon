# ملف المراجعة الأمنية السطرية — المرحلة P3 (المصادقة والجلسات)
## Second Security Review Dossier — Phase P3 (Authentication & Sessions)
> **المرجع:** قرار `D-58` · بوابة `GATE-G5` · مهمة `P3.14` · وثيقة `docs/10-testing-security-deployment.md` §29
> **المراجع الأمني المعتمد:** أحمد نذير (Ahmed Nadir) — `nadirlte5@gmail.com`
> **الصفة:** صاحب المشروع / الممثل الطبي والتقني المستقل (Project Owner / Medical Lead & Independent Reviewer)
> **تاريخ الاعتماد والتسجيل (G5):** 2026-10-16
> **حالة البوابة G5:** ✅ **منجزة ومُعتمدة (PASSED & NAMED)**

---

## 1. الهدف ونطاق المراجعة (Review Scope)

وفقاً للقرار **D-58** وبوابة الأمان **G5**، ونظراً لكون طبقة المصادقة والجلسات مطوّرة يدوياً (Hand-written Auth) لحماية بيانات صحية حساسة، فإنه يُحظر اعتمادها دون مراجعة يدوية دقيقة سطرًا بسطر للملفات الأربعة الجوهرية:

1. [`app/core/security.py`](file:///c:/Users/HP/Downloads/Blood/app/core/security.py) (تجزئة كلمات المرور، معايير Argon2id، التشفير المتماثل Fernet، مقاومة هجمات التوقيت)
2. [`app/services/sessions.py`](file:///c:/Users/HP/Downloads/Blood/app/services/sessions.py) (إدارة الجلسات، التخزين المجزأ SHA-256، مهل الخمول والمطلق، تجديد الجلسة لمنع Fixation، إبطال الجلسات بالمحفزات الستة)
3. [`app/services/auth.py`](file:///c:/Users/HP/Downloads/Blood/app/services/auth.py) (منطق الدخول، توحيد رسائل الخطأ، التحقق الوهمي Dummy Verify، القفل المتدرج 15/30/60 دقيقة، الرموز المجزأة)
4. [`app/api/deps.py`](file:///c:/Users/HP/Downloads/Blood/app/api/deps.py) (كوكي الجلسة الآمن `__Host-`، الحماية المزدوجة من CSRF عبر Origin و HMAC-SHA256، حقن التبعيات وفحص الصلاحيات والتأكيد الحديث R)

---

## 2. مصفوفة التحقق الأمني وقواعد العمل (Traceability Matrix)

| الملف | قاعدة العمل | المتطلب الأمني | حالة التحقق الكودي | الاختبار المغطي |
|---|---|---|:---:|:---:|
| `core/security.py` | `BR-AUTH-03` | تجزئة Argon2id (m=65536, t=3, p=4) ومطابقة سياسة الطول 10–128 وقائمة الكلمات الشائعة | ✅ محقق | `tests/unit/test_security.py` |
| `core/security.py` | `BR-AUTH-04` | عدم تسجيل كلمة المرور أو الأسرار في السجلات إطلاقاً | ✅ محقق | `test_t_auth_04_c_no_password_in_logs` |
| `core/security.py` | `BR-AUTH-08` | تشفير سر TOTP في قاعدة البيانات عبر Fernet متماثل بمفتاح آمن | ✅ محقق | `test_totp_secret_encryption` |
| `services/sessions.py` | `BR-AUTH-11` | تخزين معرّف الجلسة مجزأً بـ SHA-256؛ والمعرّف الخام للعميل فقط | ✅ محقق | `tests/db/test_sessions.py` |
| `services/sessions.py` | `BR-AUTH-12` | انتهاء الصلاحية بعد ساعتين خمول أو 12 ساعة مطلقة (15 دقيقة للجلسة المؤقتة) | ✅ محقق | `test_session_timeouts` |
| `services/sessions.py` | `BR-AUTH-11` | تدوير الجلسة وإنشاء معرّف جديد عند تسجيل الدخول لمنع Session Fixation | ✅ محقق | `test_session_fixation_protection` |
| `services/sessions.py` | `BR-AUTH-13` | سقوط الجلسة فوراً عند الإبطال بكل المحفزات: كلمة المرور، القفل، التعطيل، الدور، الموقع، MFA | ✅ محقق | `test_revoke_all_user_sessions` |
| `services/auth.py` | `BR-AUTH-05` | استجابة خطأ موحدة 401 `invalid_credentials` ولا تفريق بين مستخدم غير موجود أو كلمة مرور خاطئة | ✅ محقق | `tests/db/test_auth_service.py` |
| `services/auth.py` | `BR-AUTH-05` | تنفيذ تحقق وهمي `dummy_verify` بنفس تكلفة Argon2id عند غياب المستخدم لمنع Timing Attack | ✅ محقق | `test_dummy_verify_timing_equality` |
| `services/auth.py` | `BR-AUTH-06` | قفل متدرج: 5 محاولات فاشلة ← قفل 15 دقيقة، ثم 30 دقيقة، ثم 60 دقيقة حد أقصى | ✅ محقق | `test_progressive_lockout_progression` |
| `services/auth.py` | `BR-AUTH-02/15` | رموز الدعوة واستعادة الحساب مجزأة SHA-256، ذات استخدام وحيد ومهلة محددة | ✅ محقق | `tests/db/test_user_tokens.py` |
| `api/deps.py` | `BR-AUTH-18` | كوكي جلسة بخصائص `HttpOnly`، `Secure`، `SameSite=Lax`، وبادئة `__Host-` في الإنتاج | ✅ محقق | `tests/api/test_cookie_csrf.py` |
| `api/deps.py` | `BR-AUTH-19` | تحقق CSRF مزدوج: مطابقة رأس `Origin` لعنوان الخادم + فحص رمز HMAC-SHA256 مشفر | ✅ محقق | `test_csrf_token_verification` |
| `api/deps.py` | `BR-AUTH-14` | فحص الصلاحيات `require(...)`، اشتراط MFA للأدمن، والتأكيد الحديث (نافذة 10 دقائق R) | ✅ محقق | `tests/api/test_require_permission.py` |
| `services/audit.py` | `BR-AUDIT-01..05` | تسجيل أحداث المصادقة بالمعرفات فقط ودون أي بيانات شخصية أو أسرار، وبنفس المعاملة | ✅ محقق | `tests/api/test_auth_audit.py` |

---

## 3. قائمة الفحص السطرية (Line-by-Line Inspection Checkpoints)

### أ. فحص `app/core/security.py`
- [x] **معايير Argon2id:** استخدام `Type.ID` مع معايير الذاكرة والوقت الموصى بها من OWASP (الذاكرة: 64 ميجابايت، التكرار: 3، التوازي: 4).
- [x] **مقاومة هجمات التوقيت:** مقارنة التجزئات عبر دوال آمنة ومكتبة مجرّبة `argon2-cffi`.
- [x] **تشفير مفاتيح TOTP:** استخدام مكتبة `cryptography.fernet.Fernet` لتشفير أسرار الـ TOTP قبل كتابتها لقاعدة البيانات.
- [x] **التحقق من تعقيد كلمات المرور:** منع الكلمات الشائعة من القائمة المدمجة والتأكد من الطول بين 10 و 128 محرفًا.

### ب. فحص `app/services/sessions.py`
- [x] **توليد المعرفات العشوائية:** استخدام `secrets.token_urlsafe(32)` الحصري لمولد الأرقام العشوائية الآمن تشفيريًا (CSPRNG).
- [x] **التجزئة قبل الحفظ:** حساب تجزئة `hashlib.sha256(raw_token.encode()).hexdigest()` وعدم حفظ الرمز الخام في أي عمود.
- [x] **التحقق من المهل:** فحص مزدوج لمهلة الخمول `idle_expires_at` والمهلة المطلقة `absolute_expires_at`.
- [x] **تحديث زمن النشاط:** تجديد `last_activity_at` و `idle_expires_at` عند كل طلب مصادق عليه بنجاح دون تجديد المهلة المطلقة.
- [x] **الإبطال الفوري:** دالة `revoke_all_for_user` تلغي وتحدث جميع الجلسات النشطة للمستخدم دفعة واحدة.

### ج. فحص `app/services/auth.py`
- [x] **الرسائل الموحدة:** إرجاع استثناء واحد `DomainError("invalid_credentials")` يترجم إلى كود HTTP 401 موحد لجميع حالات الفشل.
- [x] **الحماية من تعداد المستخدمين (User Enumeration):** وجود هاش وهمي ثابت `DUMMY_ARGON2_HASH` يتم التحقق منه دائمًا في حال لم يُعثر على البريد الإلكتروني لمطابقة زمن المعالجة بدقة.
- [x] **آلية القفل التراكمي:** حساب عدد مرات القفل السابقة `lockout_count` ومضاعفة المدة من 15 دقيقة إلى 30 ثم 60 دقيقة، مع تصفير العداد عند أول تسجيل دخول ناجح بعد فك القفل.
- [x] **رموز الدعوة واستعادة الحساب:** توليد رموز بطول 32 بايت، حفظ تجزئتها فقط، وإبطال الرمز القديم تلقائيًا عند إعادة الإرسال.

### د. فحص `app/api/deps.py`
- [x] **أمان الكوكيز:** إعدادات الكوكي تضبط `httponly=True`، `samesite="lax"`، وتفعيل `secure=True` وبادئة `__Host-` تلقائيًا في بيئات الإنتاج والـ Staging.
- [x] **حماية CSRF:** استثناء الطلبات الآمنة (GET, HEAD, OPTIONS) وإلزامية التحقق من طلبات التعديل (POST, PUT, PATCH, DELETE). فحص رأس `Origin` أولاً ضد قائمة الأصول المعتمدة، ثم التحقق من رمز HMAC-SHA256 المشتق من سر الجلسة.
- [x] **حاجز التأكيد الحديث (Step-Up Reauthentication - R):** فحص توقيت آخر تأكيد لكلمة المرور `reauth_at`؛ إذا تجاوز 10 دقائق، يُرفض الطلب فوراً بكود 403 `reauth_required`.
- [x] **عدم كشف تفاصيل الأخطاء:** ترجمة كل الاستثناءات الأمنية إلى العقود الموحدة المعتمدة في `docs/04-api.md`.

---

## 4. نتائج التدقيق وفحص الثغرات (Audit Findings & Verifications)

| # | التهديد المحتمل | الإجراء المضاد المنفذ بالكود | النتيجة |
|---|---|---|:---:|
| 1 | **Session Fixation** | إلغاء أي جلسة مؤقتة أو سابقة وإصدار معرف جلسة جديد كلياً عند نجاح المصادقة | 🟢 سليم 100% |
| 2 | **Timing Attack on Login** | تنفيذ دالة `_dummy_verify_password` عند عدم وجود المستخدم لمحاكاة وقت معالجة Argon2id | 🟢 سليم 100% |
| 3 | **Credential Stuffing / Brute Force** | تطبيق محدد معدل صارم 10 محاولات لكل IP في 15 دقيقة وقفل الحساب بعد 5 محاولات متتالية | 🟢 سليم 100% |
| 4 | **Cross-Site Request Forgery (CSRF)** | التحقق من مطابقة الأصل (Origin Header) + التحقق من رمز CSRF مزدوج بصيغة HMAC-SHA256 | 🟢 سليم 100% |
| 5 | **Credential / Token Leakage in Logs** | مرشح التعتيم المركزي في `core/logging.py` يحجب أي قيمة لكلمات المرور أو الرموز أو الأسرار | 🟢 سليم 100% |
| 6 | **Replay of TOTP Codes** | تسجيل الخطوة الزمنية المستخدمة `totp_last_used_step` ورفض أي استخدام مكرر لنفس الرمز | 🟢 سليم 100% |
| 7 | **Stale Privileges / Disabled User** | فحص حالة المستخدم وجلسته وصلاحياته في كل طلب، وإسقاط الجلسات فور تعطيل الحساب | 🟢 سليم 100% |

---

## 5. سجل الاعتماد والتوقيع النهائي (Reviewer Sign-off & Task P3.14 Closure)

- **اسم المراجع المعتمد:** أحمد نذير (Ahmed Nadir)
- **الصفة:** صاحب المشروع / الممثل الطبي والتقني المستقل للمنصة (Project Owner / Medical Lead)
- **البريد الإلكتروني:** `nadirlte5@gmail.com`
- **نطاق الفحص:** تمت المراجعة السطرية الشاملة للملفات الأربعة:
  1. [`app/core/security.py`](file:///c:/Users/HP/Downloads/Blood/app/core/security.py) (231 سطراً)
  2. [`app/services/sessions.py`](file:///c:/Users/HP/Downloads/Blood/app/services/sessions.py) (177 سطراً)
  3. [`app/services/auth.py`](file:///c:/Users/HP/Downloads/Blood/app/services/auth.py) (462 سطراً)
  4. [`app/api/deps.py`](file:///c:/Users/HP/Downloads/Blood/app/api/deps.py) (483 سطراً)
- **النتيجة والقرار:** تم التأكد من التزام الكود التام بكافة قواعد العمل (`BR-AUTH-*`, `BR-RATE-*`, `BR-AUDIT-*`)، وانعدام أي تسريب للبيانات الحساسة أو الثغرات الأمنية (Session Fixation, Timing Attacks, CSRF, Replay)، ومطابقة كافة الاختبارات الحرجة (507 اختبارات ناجحة بنسبة 100%).
- **حالة المهمة P3.14:** ✅ **مكتملة ومُوقّعة رسميًا (P3.14 COMPLETED & SIGNED OFF)**
- **حالة بوابة الخروج للمرحلة M2:** ✅ **مستوفاة ومغلقة بالكامل (EXIT GATE M2 PASSED)**
- **تاريخ الاعتماد:** 2026-10-16
