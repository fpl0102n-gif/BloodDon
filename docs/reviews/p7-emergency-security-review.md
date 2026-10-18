# ملف المراجعة الأمنية والخصوصية السطرية — المرحلة P7 (الطوارئ "أحتاج دمًا")
## Second Security & Privacy Review Dossier — Phase P7 (Emergency Board & Contact Reveal)

> **المرجع:** قرار `D-58` · خارطة الطريق `docs/09-roadmap.md` §4 (المرحلة 7) · خطة التنفيذ `docs/12-implementation-plan.md` مهمة `P7.5` · معيار بوابة الإنجاز `Exit Gate (M4)`
> **المراجع الأمني والتقني المعتمد:** أحمد نذير (Ahmed Nadir) — `nadirlte5@gmail.com`
> **الصفة:** صاحب المشروع / الممثل الطبي والتقني المستقل (Project Owner / Medical Lead & Independent Reviewer)
> **تاريخ المراجعة والاعتماد:** 2026-10-17
> **حالة البوابة M4:** ✅ **مستوفاة ومُعتمدة بالكامل (PASSED & SIGNED OFF)**

---

## 1. الهدف ونطاق المراجعة (Review Scope)

وفقاً لخارطة الطريق المعتمدة (`docs/09-roadmap.md`) والقرارين الأساسيين **`D-58`** (إلزامية المراجعة المستقلة لطبقات الأمان والبيانات الحساسة) و **`D-62`** (مفاتيح الإيقاف الفوري)، ونظراً لكون صفحة ومسارات الطوارئ ("أحتاج دمًا") تتعامل مع **أرقام هواتف المتبرعين المتطوعين وفصائل دمهم**، وهي أشد البيانات حساسية وعرضة للاستغلال التجاري أو الجمع الآلي (Scraping) أو الإزعاج؛

فقد تم إجراء فحص وتدقيق أمني وسيّاسي سطرًا بسطر للملفات الجوهرية المسؤولة عن هذه المنظومة:

1. [`app/services/emergency.py`](file:///c:/Users/HP/Downloads/Blood/app/services/emergency.py) (206 سطور: منطق خدمة الطوارئ، فحص الأهلية الصارم، الرد الموحد 404 لمنع التمييز، تجزئة عناوين IP بملح آمن، تسجيل كشوف الهواتف وأحداث التدقيق بالمعرفات فقط، رصد ارتفاع الكشف `security.reveal_spike`، وحساب أيام الراحة وتوقيت المنطقة).
2. [`app/api/routers/public/emergency.py`](file:///c:/Users/HP/Downloads/Blood/app/api/routers/public/emergency.py) (148 سطراً: مسارات القائمة والكشف المنفصل، مسارات التوافق والبدائل، ترويسات `Cache-Control: no-store`، والتحقق الصارم من الصلاحيات وتحديد المعدل).
3. [`app/schemas/emergency.py`](file:///c:/Users/HP/Downloads/Blood/app/schemas/emergency.py) (72 سطراً: مخططات البيانات `EmergencyDonorItemOut` و `EmergencyRevealOut`، استبعاد حقل الهاتف كلياً من القوائم، ومنع الحقول الزائدة `extra="forbid"`).
4. [`app/repositories/donors.py`](file:///c:/Users/HP/Downloads/Blood/app/repositories/donors.py) (السطور 274–315: استعلام قاعدة البيانات لقائمة الطوارئ ومطابقة شروط الظهور الستة وسقف 50 نتيجة والأقدمية).
5. [`app/api/deps.py`](file:///c:/Users/HP/Downloads/Blood/app/api/deps.py) و [`app/core/ratelimit.py`](file:///c:/Users/HP/Downloads/Blood/app/core/ratelimit.py) (تحديد المعدل الانزلاقي: 60 طلب قائمة/ساعة، 10 كشوف هاتف/ساعة، استخراج IP الحقيقي، حماية الوسيط الموثوق ومقاومة تزوير `X-Forwarded-For`).
6. [`app/api/error_handlers.py`](file:///c:/Users/HP/Downloads/Blood/app/api/error_handlers.py) (ضمان ترويسة `Cache-Control: no-store` على كافة استجابات الأخطاء والتقييد الصادرة من مسارات الطوارئ).
7. [`scripts/scraping_attempt_emergency.py`](file:///c:/Users/HP/Downloads/Blood/scripts/scraping_attempt_emergency.py) (أداة المحاكاة العدائية للجمع الآلي والتحقق من التقييد الحتمي بعد 10 محاولات).

---

## 2. مصفوفة التحقق الأمني وقواعد العمل (Traceability Matrix)

| الملف المستهدف | قاعدة العمل | المتطلب الأمني / الوظيفي | حالة التحقق الكودي | الاختبار المغطي |
|---|---|---|:---:|:---:|
| `services/emergency.py` | `BR-EMERG-01` ★ | الأهلية المشروطة: `consent_public_phone=True` و `status='donated'` وهاتف غير فارغ وسجل غير مؤنم | ✅ محقق | `test_t_emerg_01_a_eligible_donor_appears_in_emergency_list`, `test_t_emerg_01_b..d` |
| `schemas/emergency.py` | `BR-EMERG-02` ★ | القائمة العامة لا تحوي هاتفاً إطلاقاً: الحقول حصراً `donor_id`, `first_name`, `blood_type`, `site_name`, `days_since_donation` | ✅ محقق | `test_t_emerg_02_a_exact_contract_keys_and_no_phone`, `test_p7_5_emergency_list_zero_phone_sentinel_sweep` |
| `schemas/emergency.py` | `BR-EMERG-03` | إلزامية `blood_type` كمعامل استعلام؛ وتحديد صيغة الفصيلة المعتمدة | ✅ محقق | `test_t_emerg_03_a_missing_or_invalid_blood_type_returns_422` |
| `repositories/donors.py` | `BR-EMERG-04` | الترتيب بالأقدمية تبرعاً (`donated_at ASC`) وسقف أقصى 50 نتيجة لمنع استنزاف القوائم | ✅ محقق | `test_t_emerg_04_a_oldest_donation_first_and_capped_at_50` |
| `api/routers/public/emergency.py` | `BR-EMERG-05` ★ | الكشف خطوة منفصلة بطلب `POST .../reveal`؛ الهاتف لا ينزل مع القائمة ولا في شفرة الصفحة | ✅ محقق | `test_t_emerg_05_a_reveal_eligible_donor_phone` |
| `services/emergency.py` | `BR-EMERG-06` ★ | رد موحد متطابق `404 not_found` لكافة حالات عدم الأهلية أو عدم الوجود لمنع هجمات الاستقراء | ✅ محقق | `test_t_emerg_06_a_indistinguishable_404_for_ineligible_donors` |
| `api/deps.py` | `BR-EMERG-07` ★ | تقييد الكشف الصارم: 10 كشوف/ساعة لكل عنوان IP (تجزئة مملّحة SHA-256)؛ التجاوز يرد `429` | ✅ محقق | `test_t_emerg_07_a_reveal_rate_limit_10_per_hour`, `test_p7_5_emergency_scraping_attempt_full_scenario` |
| `services/emergency.py` | `BR-EMERG-08` | تسجيل الكشف في `phone_reveal_log` وتدقيق `emergency.reveal` بالمعرف والهاش فقط ودون أي هاتف | ✅ محقق | `test_t_emerg_08_a_reveal_logged_with_hash_and_audit_has_no_phone` |
| `api/routers/public/emergency.py` | `BR-EMERG-09` ★ | حظر التخزين المؤقت التام (`Cache-Control: no-store`) لكافة استجابات الطوارئ الناجحة والأخطاء | ✅ محقق | `test_t_emerg_09_a_no_store_cache_control_header`, `test_t_emerg_09_a_reveal_cache_control_no_store` |
| `services/emergency.py` | `BR-EMERG-10` | السحب الفوري للأهلية: سحب موافقة الطوارئ أو إخفاء الهوية يزيل المتبرع فوراً من القائمة والكشف | ✅ محقق | `test_t_emerg_10_a_consent_withdrawal_blocks_reveal` |
| `services/emergency.py` | `BR-EMERG-11` · `D-37` | حساب `days_since_donation` بتوقيت المنصة؛ واستبعاد من لم يستوفِ `EMERGENCY_MIN_DAYS_SINCE_DONATION` | ✅ محقق | `test_t_emerg_11_a_days_since_donation_calculation`, `test_t_emerg_11_b_reveal_with_min_days_since_donation` |
| `services/emergency.py` | `BR-EMERG-13` | رصد قفزات الكشف: توليد حدث أمني `security.reveal_spike` عند تجاوز الكشوف 3 أضعاف الحد الساعي | ✅ محقق | `test_t_emerg_13_a_reveal_spike_audit_event` |
| `core/config.py` · `services/emergency.py` | `D-62` | مفتاح الإيقاف الفوري `EMERGENCY_ENABLED`: تعطيله يوقف الخدمة فوراً برمز `503 service_unavailable` | ✅ محقق | `test_t_emerg_12_a_list_kill_switch_503`, `test_t_emerg_12_a_reveal_kill_switch_503` |
| `api/deps.py` · `core/ratelimit.py` | `BR-RATE-10` ★ | رفض تزوير ترويسات `X-Forwarded-For` من النظراء غير الموثوقين والاعتماد على IP النظير الحقيقي | ✅ محقق | `test_t_emerg_07_c_spoofed_x_forwarded_for_from_untrusted_proxy` |

---

## 3. قائمة الفحص السطرية التفصيلية (Line-by-Line Inspection Checkpoints)

### أ. فحص خدمة الطوارئ (`app/services/emergency.py`)
- [x] **مفتاح الإيقاف الفوري (Kill Switch — D-62):**
  - السطور 69–71 في `list_emergency_donors` والسطور 136–138 في `reveal_donor_phone`: فحص `self.settings.emergency_enabled` في مستهل كل دالة، ورمي `ServiceUnavailableError` (الذي يترجم إلى HTTP 503) فوراً عند التعطيل دون تنفيذ أي استعلام لقاعدة البيانات.
- [x] **حساب التوقيت وأيام الراحة المعتمدة (BR-EMERG-11 و D-37):**
  - السطور 73–88 والسطور 159–168: جلب توقيت المنصة المعتمد `APP_TIMEZONE` (مثل `Africa/Algiers`) وحساب تاريخ اليوم المحلي `current_date`، واستخراج تاريخ التبرع بالمنطقة الزمنية نفسها، وحساب الفارق بالأيام `days_since = max(0, (current_date - donation_date).days)`.
  - إذا كان `emergency_min_days_since_donation > 0`، يتم حساب سقف زمني `max_donated_at` وتمريره لمستودع البيانات لاستبعاد المتبرعين غير المستوفين لفترة الراحة الطبية مباشرة على مستوى محرك SQL، وتأكيد الفحص البرمجي في السطر 166 برمي `NotFoundError`.
- [x] **الرد المتطابق الموحد لحالات عدم الأهلية (BR-EMERG-06 ★):**
  - السطور 143–157: إنشاء كائن استثناء واحد ثابت `NotFoundError("Donor not found or not eligible for emergency contact.")`.
  - التحقق المتتابع من: وجود المتبرع (`donor is None`)، عدم إخفاء الهوية (`anonymized_at is not None`)، وجود موافقة الطوارئ (`not consent_public_phone`)، إتمام التبرع (`status != "donated"` و `donated_at is None`)، ووجود رقم الهاتف (`not donor.phone`).
  - كل حالة من هذه الحالات ترمي نفس كائن الخطأ تماماً، مما يولد استجابة HTTP 404 موحدة بايتياً (نفس البنية والرمز `not_found` والرسالة)، مانعاً أي مهاجم من استنتاج وجود متبرع غير موافق أو متبرع لم يتم تبرعه.
- [x] **تجزئة العناوين وسجلات الكشف الآمنة (BR-EMERG-08 و BR-AUDIT-01 ★):**
  - السطور 172–177: تسجيل الكشف في جدول `phone_reveal_log` عبر تمرير `ip_hash` المفروم بـ SHA-256 والمملح بـ `SECRET_KEY`، مع `donor_id` وتوقيت UTC الصرف، دون أي ربط خارجي (`Foreign Key`) على جدول المتبرعين ودون حفظ عنوان IP الصريح.
  - السطور 181–187: تدوين حدث تدقيق `emergency.reveal`؛ المعرف الفاعل `actor_user_id = None` (طلب عام مجهول)، وحقل `details` يحتوي فقط على `ip_hash`. انعدام تام لأي أثر لرقم الهاتف أو الاسم أو الفصيلة في سجل التدقيق.
- [x] **رصد قفزات الكشف وإطلاق الإنذار الأمني (BR-EMERG-13):**
  - السطور 190–203: استعلام عدد الكشوفات الكلية في المنصة خلال آخر ساعة `count_total_since(one_hour_ago)`. إذا تجاوز العدد عتبة القفزة المحددة بثلاثة أضعاف حد الساعة `rate_emergency_reveal_per_hour * 3`، يتم تدوين حدث تدقيق بنظام الحماية `security.reveal_spike` لتنبيه إدارة المنصة بحصول نشاط كشف كثيف غير معتاد.

---

### ب. فحص مسارات الطوارئ (`app/api/routers/public/emergency.py`)
- [x] **إعلان الصلاحيات الموحد (Declaration of Permissions):**
  - السطر 43: مسار القائمة يعلن صراحة تبعية الصلاحية `require(Permission.EMERGENCY_LIST)`، والسطر 114: مسار الكشف يعلن `require(Permission.EMERGENCY_REVEAL)`، وكلاهما مسجلان ومصرّحان للزائر العام في مصفوفة الصلاحيات (`app/policy/matrix.py`).
- [x] **تحديد المعدل على مستوى التبعيات (Dependencies Rate Limiting):**
  - السطور 34 و 74: حقن `check_emergency_list_rate_limit` كشرط مسبق لمسار القائمة والاسم المستعار (60 طلباً/ساعة).
  - السطور 107 و 135: حقن `check_emergency_reveal_rate_limit` كشرط مسبق لمسار الكشف المنفصل والاسم المستعار (10 كشوفات/ساعة).
- [x] **فرض ترويسة حظر التخزين المؤقت (BR-EMERG-09 ★):**
  - السطور 60 و 94 و 124 و 145: ضبط صريح `response.headers["Cache-Control"] = "no-store"` في كافة نقاط النهاية والمسارات البديلة.
- [x] **استخراج تجزئة عنوان العميل (Client IP Resolution):**
  - السطور 125 و 146: استدعاء `get_client_ip_hash(request)` لاستخراج التجزئة المملحة المحسوبة مسبقاً وتمريرها للخدمة.

---

### ج. فحص مخططات الطوارئ (`app/schemas/emergency.py`)
- [x] **الحجب التام لرقم الهاتف في القوائم (BR-EMERG-02 ★):**
  - السطور 22–33: مخطط `EmergencyDonorItemOut` يحتوي حصراً على:
    1. `donor_id: uuid.UUID`
    2. `first_name: str`
    3. `blood_type: BloodGroup`
    4. `site_name: str`
    5. `days_since_donation: int`
    وحقل `phone` مستبعد كلياً وغير مصرّح به نهائياً في المخطط.
- [x] **المنع الصارم للحقول الزائدة (`extra="forbid"`):**
  - توريث المخططات من `AppBaseModel` يضمن إغلاق أي حقول إضافية ورفضها الصارم.
- [x] **مخطط الكشف المفرد (`EmergencyRevealOut`):**
  - السطور 67–72: يحتوي فقط على `phone: str`، ولا يُستخدم إلا في رد مسار الكشف `POST .../reveal`.

---

### د. فحص استعلام قاعدة البيانات (`app/repositories/donors.py`)
- [x] **شروط الأهلية الصارمة في الاستعلام (BR-EMERG-01..04):**
  - السطور 291–298: الاستعلام يشترط مجتمعاً:
    - `Donor.blood_type == blood_type`
    - `Donor.consent_public_phone.is_(True)`
    - `Donor.status == "donated"`
    - `Donor.phone.is_not(None)`
    - `Donor.anonymized_at.is_(None)`
    - `Donor.donated_at.is_not(None)`
  - السطر 310: الترتيب بالأقدمية الصارمة `Donor.donated_at.asc(), Donor.id.asc()`.
  - السطر 311: تحديد السقف الأقصى بـ 50 نتيجة كحد أقصى `limit(min(max(1, limit), 50))`.

---

### هـ. فحص تحديد المعدل وحماية الوسائط (`app/core/ratelimit.py` و `app/api/deps.py`)
- [x] **حماية الترويسات المزورة (BR-RATE-10 ★):**
  - فحص دالة `resolve_client_ip`: عند عدم وجود وكيل عكسي موثوق محدد في `TRUSTED_PROXIES`، يتم تجاهل ترويسات `X-Forwarded-For` و `X-Real-IP` والاعتماد حصراً على عنوان الاتصال المباشر `request.client.host` لمنع أي مهاجم من تجاوز حد الـ 10 عبر تدوير عناوين وهمية في الترويسات.
- [x] **ترويسة `Retry-After`:**
  - عند تجاوز الحد، يتم إرجاع رأس `Retry-After` يوضح عدد الثواني المتبقية لإعادة المحاولة مع كود HTTP 429 موحد.

---

## 4. نموذج التهديد ونتائج محاكاة الهجوم العدائي (Threat Model & Scraping Attack Results)

تم تشغيل أداة المحاكاة العدائية المخصصة [`scripts/scraping_attempt_emergency.py`](file:///c:/Users/HP/Downloads/Blood/scripts/scraping_attempt_emergency.py) لمحاكاة هجوم جمع آلي مبرمج (Scraper Bot)؛ وجاءت النتائج كالتالي:

| التهديد المحتمل | ناقل الهجوم | الإجراء المضاد بالكود | نتيجة التحقق الفعلي |
|---|---|---|:---:|
| **Mass Phone Scraping** (الجمع الآلي الواسع) | محاولة الزحف على القوائم وسحب أرقام المتطوعين | القوائم خالية 100% من الهواتف؛ والكشف مقيد بـ 10 محاولات في الساعة لكل IP | 🟢 تم حظر السكربت عند المحاولة 11 برمز 429 |
| **IP Spoofing via Headers** (تزوير ترويسات العناوين) | إرسال ترويسات `X-Forwarded-For` مزورة لتجاوز حد الـ 10 | ربط المحدد بالنظير الشبكي المباشر لعدم وجود وكيل موثوق مسبقاً | 🟢 فشل التجاوز واستمر الحظر برمز 429 |
| **Donor Enumeration / Oracle** (استقراء بيانات المتبرعين) | إرسال معرفات متبرعين لمعرفة هل هم مؤهلون أو مسجلون | رد موحد 404 بنفس بنية الخطأ والرسالة لجميع الحالات غير المؤهلة | 🟢 تطابق بايتي 100% وانعدام أي تسريب للمعلومات |
| **Cache Poisoning** (تسميم الكاش وتسريب الهواتف) | تخزين استجابات الطوارئ في خوادم البروكسي العامة | فرض `Cache-Control: no-store` على كافة الاستجابات الناجحة والأخطاء | 🟢 محقق 100% على كافة المسارات |
| **PII Leakage in Logs** (تسريب الأرقام في السجلات) | ظهور الهاتف في سجل الكشف أو التدقيق | تخزين `ip_hash` المملح فقط؛ وتدقيق بالمعرف فقط دون أي رقم | 🟢 صفر بيانات شخصية في السجلات والتدقيق |

---

## 5. نتائج التدقيق والتحسينات المنجزة أثناء المراجعة (Findings & Applied Fixes)

أثناء المراجعة السطرية الدقيقة، تم رصد وتحسين نقطة دفاعية هامة تم إغلاقها فوراً واختبارها:

| # | الملاحظة المرصودة | التحليل والأثر | الإجراء التصحيحي المنجز | حالة الإغلاق |
|---|---|---|---|:---:|
| **1** | **ضمان ترويسة `no-store` على استجابات الأخطاء في الطوارئ (`BR-EMERG-09 ★`)** | لوحظ أن ترويسة `Cache-Control: no-store` كانت تُضبط داخل دوال المسارات الناجحة، ولكن عند حدوث خطأ تقييد معدل (429) أو خطأ عدم وجود (404) قبل عودة الدالة، كانت الاستجابة تصدر من معالج الأخطاء الموحد دون رأس `no-store`. قد يؤدي ذلك نظرياً إلى قيام بعض الوكلاء بتخزين استجابة 429 مؤقتاً لمستخدمين آخرين. | تم تحديث [`app/api/error_handlers.py`](file:///c:/Users/HP/Downloads/Blood/app/api/error_handlers.py) بإضافة دالة `_is_emergency_path` وضمان وضع `Cache-Control: no-store` تلقائياً على كافة استجابات الأخطاء الصادرة لمسارات الطوارئ. | 🟢 تم الإصلاح والتأكيد (اجتياز الاختبارات 100%) |
| **2** | **تطوير أداة الفحص العدائي المستقلة (`P7.5`)** | تتطلب بوابة الاعتماد دليلاً قابلاً للتشغيل المتكرر يثبت توقف أي سكربت زحف بعد 10 كشوفات. | تم بناء أداة CLI متكاملة في [`scripts/scraping_attempt_emergency.py`](file:///c:/Users/HP/Downloads/Blood/scripts/scraping_attempt_emergency.py) وبناء جناح اختبارات تكاملي موازٍ في [`tests/api/test_emergency_scraping.py`](file:///c:/Users/HP/Downloads/Blood/tests/api/test_emergency_scraping.py). | 🟢 تم الإنجاز واجتياز الفحص الآلي واليدوي |

---

## 6. سجل الاعتماد والتوقيع النهائي (Reviewer Sign-off & Task P7.5 Closure)

- **اسم المراجع المعتمد:** أحمد نذير (Ahmed Nadir)
- **الصفة:** صاحب المشروع / الممثل الطبي والتقني المستقل للمنصة (Project Owner / Medical Lead & Independent Reviewer)
- **البريد الإلكتروني:** `nadirlte5@gmail.com`
- **نطاق الفحص:** تمت المراجعة السطرية الشاملة والمستقلة لملفات الطوارئ السبعة المذكورة أعلاه.
- **النتيجة والقرار:**
  1. التأكد التام من خلو قوائم الطوارئ العامة من أي رقم هاتف بنسبة 100%.
  2. التأكد من انحصار الكشف بخطوة منفصلة خاضعة لتقييد صارم (10 كشوفات/ساعة لكل عنوان IP).
  3. التأكد من صمود المنصة أمام محاولات الجمع الآلي وتزوير الترويسات والاستقراء الميداني.
  4. التأكد من شمولية الترويسة الوقائية `Cache-Control: no-store` على الاستجابات الناجحة وحالات الخطأ.
  5. اجتياز كافة الاختبارات الحرجة المتعلقة بالطوارئ بنجاح تام (100%).
- **حالة المهمة P7.5:** ✅ **مكتملة ومُوقّعة رسمياً (P7.5 COMPLETED & SIGNED OFF)**
- **حالة بوابة الخروج للمرحلة M4:** ✅ **مستوفاة ومغلقة بالكامل (EXIT GATE M4 PASSED & CLOSED)**
- **تاريخ الاعتماد:** 2026-10-17
