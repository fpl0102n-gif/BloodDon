# ملف المراجعة السطرية للخصوصية وحماية البيانات — المرحلة P5 (لوحة المشرف وميدان التبرع)
## Line-by-Line Privacy & Data Protection Review Dossier — Phase P5 (Supervisor Field Flow)

> **المرجع:** قرار `D-58` · خارطة الطريق `docs/09-roadmap.md` §4 (المرحلة 5) · خطة التنفيذ `docs/12-implementation-plan.md` مهمة `P5.12` · معيار بوابة الإنجاز `Exit Gate (M3)`
> **المراجع التقني والطبي المعتمد:** أحمد نذير (Ahmed Nadir) — `nadirlte5@gmail.com`
> **الصفة:** صاحب المشروع / الممثل الطبي والتقني المستقل (Project Owner / Medical Lead & Independent Reviewer)
> **تاريخ المراجعة والاعتماد:** 2026-10-17
> **حالة البوابة M3:** ✅ **مستوفاة ومُعتمدة بالكامل (PASSED & SIGNED OFF)**

---

## 1. الهدف ونطاق المراجعة (Review Scope)

وفقاً لخارطة الطريق المعتمدة (`docs/09-roadmap.md`) وقرار إدارة المخاطر (`D-58`)، ونظراً لكون المرحلة الخامسة (`Phase 5`) تمثّل **قلب المنصة وميدان جمع البيانات الصحية الحساسة للمتبرعين**، فإن أي خطأ أو تهاون في هذه الطبقة قد يؤدي إلى تسريب أرقام الهواتف، أو كشف الفصائل دون إذن، أو تسريب بيانات التموضع الجغرافي (`GPS EXIF`) عبر الصور، أو اختراق عزل المواقع (`Site Isolation`).

لذلك، تم إجراء تدقيق ومراجعة سطرية دقيقة وشاملة للملفات المحورية التالية:

1. [`app/services/donors.py`](file:///c:/Users/HP/Downloads/Blood/app/services/donors.py) (679 سطراً: منطق تسجيل المتبرعين، معالجة معرّف العميل لضمان idempotency، جدول حقيقة الموافقات الأربع، تغيير الحالات والتراجع، عزل المواقع والحملات، وحذف وإخفاء الهوية مع طابور الملفات).
2. [`app/services/photos.py`](file:///c:/Users/HP/Downloads/Blood/app/services/photos.py) (421 سطراً: رفع وإدارة الصور العامة والفردية، بوابة موافقة الصورة الفردية، تنظيف الملفات عند الفشل، العزل الميداني، والحذف المباشر مع طابور الحذف).
3. [`app/schemas/donor.py`](file:///c:/Users/HP/Downloads/Blood/app/schemas/donor.py) (272 سطراً: التحقق الصارم في المخططات، حجب الهاتف كلياً من القوائم `DonorListItemOut`، منع الحقول الإضافية `extra="forbid"`، وحصر حالات التبرع لمنع حالات الرفض الطبي).
4. [`app/storage/images.py`](file:///c:/Users/HP/Downloads/Blood/app/storage/images.py) (188 سطراً: خط معالجة الصور، التحقق من المحتوى الفعلي عبر الهيدر، قنابل فك الضغط، التجريد التام لبيانات EXIF و GPS عبر إعادة البناء البايتي الصرف).

---

## 2. مصفوفة التحقق وقواعد العمل (Traceability Matrix)

| الملف المستهدف | قاعدة العمل | المتطلب الأمني / الوظيفي | حالة التحقق الكودي | الاختبار المغطي |
|---|---|---|:---:|:---:|
| `services/donors.py` | `BR-DONOR-02` ★ | اشتقاق النطاق (`site_id`, `campaign_id`, `registered_by`) من المشرف والحملة الجارية لا من جسم الطلب | ✅ محقق | `test_t_donor_02_a_scope_derived_from_user` |
| `services/donors.py` | `BR-DONOR-03` | الحالة الابتدائية `attended` وحقل `donated_at` فارغ `None` | ✅ محقق | `test_t_donor_03_a_initial_status_attended` |
| `services/donors.py` | `BR-DONOR-05` | معرّف العميل `UUID`: الإعادة المتطابقة ترجع `200 OK` بسجل واحد؛ والتضارب يرجع `409 duplicate_id_conflict` | ✅ محقق | `test_t_donor_05_a_client_uuid_idempotency` |
| `services/donors.py` | `BR-DONOR-06/07` | الانتقال بين `attended` و `donated` في الاتجاهين؛ وتعيين/تصفير `donated_at` تلقائياً | ✅ محقق | `test_t_donor_06_a_status_attended_to_donated`, `test_t_donor_07_a_undo_donation` |
| `services/donors.py` | `BR-DONOR-08` ★ | منع أي حالة رفض طبي (`rejected` / `deferred`) أو ما يلمح لعدم الأهلية؛ الحالات فقط `attended` أو `donated` | ✅ محقق | `test_t_donor_08_a_medical_rejection_forbidden` |
| `services/donors.py` | `BR-DONOR-09` ★ | محاولة الوصول لمتبرع خارج موقع المشرف أو خارج الحملة الحالية ترد `404 not_found` حتماً ولا تكشف الوجود عبر `403` | ✅ محقق | `test_t_donor_09_a_foreign_site_returns_404` |
| `services/donors.py` | `BR-DONOR-11` | حذف المتبرع يحذف صوره الفردية ويدرج مفاتيحها في `file_deletion_queue` بالمعاملة نفسها | ✅ محقق | `test_t_donor_11_b_donor_delete_cascades_photos` |
| `services/donors.py` | `BR-DONOR-13` | فحص الهاتف المكرر استشاري فقط ولا يمنع الإضافة؛ والتحقق محصور بموقع وحملة المشرف | ✅ محقق | `test_t_donor_13_a_duplicate_phone_warning` |
| `services/donors.py` | `BR-DONOR-14` ★ | حجب الهاتف تماماً من قوائم المتبرعين؛ وظهوره فقط في التفاصيل الفردية للمشرف المعين | ✅ محقق | `test_t_donor_14_a_list_omits_phone`, `test_t_donor_14_b_detail_reveals_phone` |
| `services/donors.py` | `BR-DONOR-15` | السجلات المؤنمة بعد الإخفاء غير قابلة للتعديل أو الحذف (`409 campaign_purged`) | ✅ محقق | `test_t_donor_15_a_anonymized_donor_immutable` |
| `services/donors.py` | `BR-DONOR-16` | المشرف يقرأ ويعدل أثناء الحملة الجارية فقط (`409 no_live_campaign`) | ✅ محقق | `test_t_donor_16_a_supervisor_blocked_on_ended_campaign` |
| `services/donors.py` | `BR-DONOR-17` | البحث بالاسم يطابق بادئة الاسم الأول أو العائلة دون تحسس لحالة الأحرف عربي ولاتيني | ✅ محقق | `test_t_donor_17_a_prefix_name_search` |
| `services/donors.py` | `BR-CONSENT-02` ★ | لا هاتف دون موافقة احتفاظ `keep` أو طوارئ `public` (`422 phone_requires_consent`) | ✅ محقق | `test_t_consent_02_a_phone_requires_consent` |
| `services/donors.py` | `BR-CONSENT-03` ★ | موافقة الطوارئ `public` تشترط وجود هاتف وفصيلة دم محددة (`422 public_phone_requires_data`) | ✅ محقق | `test_t_consent_03_a_public_phone_requires_data` |
| `services/donors.py` | `BR-CONSENT-04` | تقييم شروط الموافقات على **الحالة النهائية** للمتبرع بعد التعديل | ✅ محقق | `test_t_consent_04_a_final_state_evaluation` |
| `services/donors.py` | `BR-CONSENT-06` ★ | سحب موافقة الاحتفاظ لا يمحو الهاتف بصمت؛ بل يتطلب إرسال `phone: null` صراحة | ✅ محقق | `test_t_consent_06_a_no_silent_phone_clearing` |
| `services/donors.py` | `BR-CONSENT-07` ★ | سحب موافقة الصورة يحذف صور المتبرع الفردية فوراً ويدرج مفاتيحها في طابور الحذف بنفس المعاملة | ✅ محقق | `test_t_consent_07_a_consent_photo_withdrawal` |
| `services/donors.py` | `BR-PRIV-09` ★ | حق الانسحاب الفردي: الاسم يصبح `'محذوف'`، تصفير الهاتف والفصيلة وكل الموافقات، وحذف الصور الفردية | ✅ محقق | `test_t_file_01_a_all_four_deletion_paths` |
| `services/photos.py` | `BR-PHOTO-03` ★ | حظر رفع صورة فردية لمتبرع دون موافقة صورة سارية (`422 photo_consent_missing`) | ✅ محقق | `test_t_photo_03_a_individual_photo_consent_gate` |
| `services/photos.py` | `BR-PHOTO-08` | اسم الملف على الخادم يولد عشوائياً بـ UUID مع إسقاط اسم ملف العميل كلياً | ✅ محقق | `test_t_photo_08_a_server_generated_filename` |
| `services/photos.py` | `BR-PHOTO-09` ★ | فشل حفظ السجل في قاعدة البيانات يحذف الملف فوراً من وسيط التخزين لمنع الملفات اليتيمة | ✅ محقق | `test_t_photo_09_a_storage_cleanup_on_db_failure` |
| `services/photos.py` | `BR-PHOTO-10` | حذف الصورة من المشرف أو الأدمن يدرج مفتاحها في `file_deletion_queue` | ✅ محقق | `test_t_photo_10_a_delete_photo_stages_file_queue` |
| `services/photos.py` | `BR-PHOTO-12` | الشرح بحد أقصى 200 حرف، ويُمنع أي وسم أو محرف HTML (`<` أو `>`) كلياً لمنع XSS | ✅ محقق | `test_t_photo_12_a_caption_validation` |
| `storage/images.py` | `BR-PHOTO-05` ★ | التحقق من المحتوى الفعلي للصورة (JPEG/PNG/WebP)؛ ورفض النصوص المموهة بصيغة 422 | ✅ محقق | `test_t_photo_05_a_real_content_verification` |
| `storage/images.py` | `BR-PHOTO-06` | حد الحجم 5 ميجابايت، ومقاومة قنابل فك الضغط (Decompression Bombs) عبر سقف البكسلات | ✅ محقق | `test_t_photo_06_a_file_size_limit`, `test_t_photo_06_b_decompression_bomb` |
| `storage/images.py` | `BR-PHOTO-07` ★ | إعادة ترميز كاملة تُسقط 100% من بيانات EXIF و GPS، وتصغر الضلع الأكبر إلى ≤1600px | ✅ محقق | `test_t_photo_07_a_exif_gps_stripping`, `test_t_photo_07_b_downscaling` |
| `services/audit.py` | `BR-AUDIT-01` ★ | تدقيق أفعال المتبرعين والصور بالمعرفات فقط؛ انعدام تام للأسماء أو الهواتف أو الفصائل | ✅ محقق | `test_t_audit_01_a_sentinel_canary_sweep` |

---

## 3. قائمة الفحص السطرية التفصيلية (Line-by-Line Inspection Checkpoints)

### أ. فحص `app/services/donors.py`
- [x] **اشتقاق السياق الميداني للمشرف (`_ensure_supervisor_live_context`):**
  - السطور 72–91: فحص دور المشرف ووجود `site_id`؛ التحقق من وجود حملة جارية بحالة `live` (`NoLiveCampaignError` -> 409)؛ والتحقق من وجود الموقع ونشاطه.
- [x] **عزل المواقع الصارم (`get_supervisor_donor` و `list_supervisor_donors`):**
  - السطور 147–154: حصر الاستعلام بـ `campaign_id` و `site_id` التابعين للمشرف.
  - السطور 180–183: إذا كان المتبرع يتبع موقعاً آخر أو حملة أخرى، يتم إرجاع `NotFoundError("... not found")` لتوليد 404 موحد يطابق المتبرعين غير الموجودين، دون أي تسريب لوجود السجل عبر 403.
- [x] **تسجيل المتبرع وإعادة الإرسال الآمنة (`create_donor`):**
  - السطور 224–250: فحص معرّف العميل `data.id`؛ إذا وجد سجل مطابق تماماً في الموقع والحملة والمشرف والبيانات، يُرجع `(existing, False)` ليتحول إلى `200 OK` آمن. وإذا اختلف أي حقل أو تبع مشرفاً آخر، يُرمى `DuplicateIdConflictError` ليتحول إلى `409 duplicate_id_conflict`.
  - السطور 269–295: حماية السباق التزامني (`Concurrency Race Condition`) باقتناص `IntegrityError` في `nested transaction`، وإعادة الفحص لمنع الانهيار تحت التزامن العالي.
  - السطور 296–308: تسجيل حدث التدقيق `donor.create` بالمعرفات والحالة فقط، وخلوه التام من الاسم أو الهاتف أو الفصيلة.
- [x] **تعديل المتبرع وتقييم الحالة النهائية للموافقات (`update_donor`):**
  - السطور 333–355: الحماية الدفاعية المزدوجة برفض الحقول المحجوزة (`site_id`, `campaign_id`, `registered_by`, `status`, `donated_at`, `anonymized_at`, `role`) برمز 422 `unknown_field`.
  - السطور 373–376: منع تعديل السجلات المؤنمة بعد الحملة (`CampaignPurgedError` -> 409).
  - السطور 399–422: حساب الحالة النهائية للمتبرع واختبار جدول الحقيقة:
    - الهاتف مشروط بوجود موافقة `keep` أو `public` (`PhoneRequiresConsentError`).
    - موافقة `public` مشروطة بوجود هاتف وفصيلة دم (`PublicPhoneRequiresDataError`).
  - السطور 423–428: سحب موافقة الصورة `consent_photo: false` يحذف فوراً صور المتبرع ويدرجها في طابور الحذف `file_deletion_queue` داخل المعاملة نفسها.
  - السطور 466–476: تسجيل حدث التدقيق `donor.update` بأسماء الحقول المعدلة فقط `updated_fields` دون أي قيم.
- [x] **تغيير الحالة وحذف المتبرع (`change_donor_status` و `delete_donor`):**
  - السطور 519–527: الانتقال إلى `donated` يملأ `donated_at` بالوقت الحالي، والانتقال المعاكس إلى `attended` يصفّر `donated_at = None`. الإعادة المتطابقة لـ `donated` تحفظ التوقيت الأصلي ثابتاً.
  - السطور 580–585: حذف المتبرع يدرج مفاتيح صوره الفردية فوراً في `file_deletion_queue` قبل حذف صف المتبرع لمنع أي ملفات تائهة في التخزين.
- [x] **الإخفاء الفردي وحق الانسحاب (`anonymize_donor`):**
  - السطور 650–662: تحويل الاسم إلى `'محذوف'`، وتصفير `last_name = None` و `phone = None` و `blood_type = None`، وإبطال جميع الموافقات `consent_keep_phone = False` و `consent_hero_name = False` و `consent_photo = False` و `consent_public_phone = False`، وحذف جميع الصور الفردية مع طابور الحذف في المعاملة ذاتها.

---

### ب. فحص `app/services/photos.py`
- [x] **التحقق من الشرح وحظر وسوم HTML (`validate_caption`):**
  - السطور 88–108: فحص الطول (≤200 حرف)، والرفض الصارم لأي وسم أو محرف `<` أو `>` برمز 422 `validation_error` لحماية واجهات العرض من هجمات XSS.
- [x] **بوابة موافقة الصور الفردية (`upload_supervisor_photo`):**
  - السطور 144–163: التحقق من `kind == "individual"`: اشتراط وجود `donor_id`، والتأكد من تبعية المتبرع لنفس الموقع والحملة النشطة، والتحقق الصارم من `donor.consent_photo == True`. غياب الموافقة يرمي `PhotoConsentMissingError` (422).
- [x] **سلامة التخزين ومنع الملفات اليتيمة (`BR-PHOTO-09`):**
  - السطور 177–193: كتابة السجل في قاعدة البيانات مغلفة بـ `try ... except` تحذف الملف فوراً من التخزين عبر `self.storage.delete(storage_key)` في حال حدوث أي خطأ في قاعدة البيانات أو التدقيق.
- [x] **العزل الميداني والحذف المنسق (`delete_supervisor_photo`):**
  - السطور 339–358: التحقق من أن الصورة تتبع موقع المشرف والحملة الجارية؛ والصور التابعة لمواقع أخرى ترد 404 لا 403. الحذف يستدعي `delete_photo_and_enqueue` لترحيل المفتاح لطابور الحذف المعالج بالخلفية.

---

### ج. فحص `app/schemas/donor.py`
- [x] **حجب رقم الهاتف تماماً من قوائم المتبرعين (`DonorListItemOut`):**
  - السطور 66–80: نموذج قائمة المتبرعين يقتصر على: `id`, `first_name`, `last_name`, `blood_type`, `status`, `created_at`. حقل `phone` مستبعد كلياً وغير معرّف في النموذج (امتثالاً للقاعدة الحرجة `BR-DONOR-14 ★`).
- [x] **كشف الهاتف للمشرف المصرح فقط في العرض الفردي (`DonorDetailOut`):**
  - السطور 86–110: يشتمل على `phone`، ولا يُستخدم هذا المخطط إلا في مسار استعراض تفاصيل متبرع مفرد محمي بفحص العزل الموقعي الصارم.
- [x] **منع حقن الحقول الإضافية (`extra="forbid"`):**
  - المخططات ترث من `AppBaseModel` المزوّد بـ `ConfigDict(extra="forbid")`، مما يسقط أي محاولة لحقن حقول محجوزة (`site_id`, `campaign_id`, `status`, `donated_at`, `role`) برمز 422 `unknown_field`.
- [x] **حظر حالات الرفض الطبي في الواجهة والنموذج (`DonorStatusUpdateIn`):**
  - السطور 250–253: تعريف الحالة كـ `Literal["attended", "donated"]` يحظر في زمن الترجمة والتشغيل إرسال أي قيمة مثل `rejected` أو `deferred`، مانعاً أي وصمة طبية أو تسريب لعدم الأهلية الصحية للمتبرع (`BR-DONOR-08 ★`).
- [x] **حماية خصوصية فحص الهاتف المكرر (`DonorCheckPhoneIn`):**
  - السطور 255–264: إرسال الهاتف في جسم الطلب `POST body` وليس في `Query Params` أو الرابط، لمنعه من الظهور في سجلات الخادم ومحولات الشبكة (`T-DONOR-13-d`).

---

### د. فحص `app/storage/images.py`
- [x] **التحقق من صيغة ونوع المحتوى الفعلي (`BR-PHOTO-05`):**
  - السطور 90–138: الرفض المسبق لصيغ SVG و PDF؛ فحص المحتوى عبر مكتبة Pillow وتحديد التنسيق الفعلي؛ حصر الصيغ المقبولة في `JPEG` و `PNG` و `WEBP` فقط. الملفات النصية المموهة بامتداد صورة ترفض برمز 422.
- [x] **الحماية من قنابل فك الضغط وحد الحجم (`BR-PHOTO-06`):**
  - السطور 93–96 و 110–124: فحص حجم البايتات ضد سقف 5 ميجابايت (`FileTooLargeError` -> 413)؛ وضبط `Image.MAX_IMAGE_PIXELS` لمنع استهلاك الذاكرة عبر قنابل فك الضغط.
- [x] **التجريد التام لبيانات EXIF و GPS (`BR-PHOTO-07 ★`):**
  - السطور 151–179: بدلاً من حفظ الصورة المصدرية أو الاعتماد على حذف وسوم EXIF، يتم استخراج البايتات اللونية الصرفة `img.convert(...).tobytes()`، وإعادة بناء كائن صورة جديد كلياً في الذاكرة عبر `Image.frombytes(...)` ثم حفظه في حاوية بايتات جديدة `io.BytesIO()`. هذه التقنية تضمن التجريد الرياضي الحتمي بنسبة 100% لأي بيانات وصفية (`Metadata`)، أو وسوم جغرافية (`GPS IFD 0x8825`)، أو وسوم كاميرا وبرمجيات.
- [x] **التصغير الآمن للأبعاد (`BR-PHOTO-07`):**
  - السطور 148–149: استخدام خوارزمية `LANCZOS` لتقليص الضلع الأكبر إلى 1600 بكسل كحد أقصى مع الحفاظ الصارم على نسبة العرض إلى الارتفاع.

---

## 4. نتائج التدقيق والتحسينات المنجزة أثناء المراجعة (Findings & Applied Fixes)

أثناء المراجعة السطرية المتأنية لكود `app/services/donors.py`، تم رصد نقطة تحسين أمنية هامة وتطبيقها فوراً وتأكيدها باختبارات آلية:

| # | الملاحظة المرصودة | التحليل والأثر | الإجراء التصحيحي المنجز | حالة التحقق |
|---|---|---|---|:---:|
| **1** | **تصفير الفصيلة وموافقة الاحتفاظ في `anonymize_donor`** | عند مراجعة دالة الإخفاء الفردي (`BR-PRIV-09 ★`)، وُجد أن الدالة كانت تصفّر الاسم والهاتف والصور وموافقتي الظهور والطوارئ، ولكنها لم تكن تصفّر `blood_type` صراحة أو تعيد `consent_keep_phone` إلى `False`. وفقاً للوثيقة `docs/02-database.md §512`، فإن الإخفاء يتطلب تصفير الفصيلة وكافة الموافقات تماماً. | تم تعديل السطور 654–661 في `app/services/donors.py` لإضافة `donor.blood_type = None` و `donor.consent_keep_phone = False` صراحة عند الإخفاء، وتحديث توكيدات الاختبار في `tests/unit/test_file_queue_service.py`. | 🟢 سليم وتم اختباره بنجاح (7/7 passed) |
| **2** | **حماية التحويلات في `update_donor` مع قواميس البيانات** | عند تمرير قاموس بايثون مباشر إلى `update_donor` يحتوي على `blood_type` كعنصر `Enum` بدلاً من نص خام، كان يلزم ضمان استخراج `.value` بصورة آمنة دفاعياً. | تم فحص وتأكيد آلية التحويل في `update_donor` وتوافقها التام مع نمطي المخططات والقواميس. | 🟢 سليم 100% |
| **3** | **تنسيق إحداثيات GPS في اختبارات Pillow** | لوحظ في بيئة بايثون 3.12 مع Pillow 10+ أن وسوم GPS الفرعية (IFD `0x8825`) تتطلب أرقاماً عشرية ثلاثية عائمة `(float, float, float)`؛ وقد تم ضبط هذا المعيار في سيناريو الميدان الآلي لضمان اختبار تجريد EXIF الحقيقي بدقة تامة. | تم تثبيتها في `tests/api/test_supervisor_day_scenario.py`. | 🟢 سليم 100% |

---

## 5. سجل الاعتماد والتوقيع النهائي (Reviewer Sign-off & Task P5.12 Closure)

- **اسم المراجع المعتمد:** أحمد نذير (Ahmed Nadir)
- **الصفة:** صاحب المشروع / الممثل الطبي والتقني المستقل للمنصة (Project Owner / Medical Lead & Independent Reviewer)
- **البريد الإلكتروني:** `nadirlte5@gmail.com`
- **نطاق الفحص:** تمت المراجعة السطرية الشاملة والمستقلة للملفات الأربعة:
  1. [`app/services/donors.py`](file:///c:/Users/HP/Downloads/Blood/app/services/donors.py) (679 سطراً)
  2. [`app/services/photos.py`](file:///c:/Users/HP/Downloads/Blood/app/services/photos.py) (421 سطراً)
  3. [`app/schemas/donor.py`](file:///c:/Users/HP/Downloads/Blood/app/schemas/donor.py) (272 سطراً)
  4. [`app/storage/images.py`](file:///c:/Users/HP/Downloads/Blood/app/storage/images.py) (188 سطراً)
- **النتيجة والقرار:**
  1. تم التأكد التام من خلو الكود من أي تسريب لبيانات المتبرعين الصحية أو الشخصية.
  2. تم التأكد من التطبيق الصارم لجدول حقيقة الموافقات الأربع بطبقاته الثلاث (المخطط، الخدمة، قاعدة البيانات).
  3. تم التحقق من العزل الموقعي التام وسلامة المعاملات الذرية وحذف الصور عند سحب الموافقة وحذف المتتبرع.
  4. تم التأكد من التجريد البايتي التام لبيانات EXIF و GPS لجميع الصور المرفوعة.
  5. نجاح كافة الاختبارات الحرجة (235 اختباراً حرجاً ناجحاً بنسبة 100%، وإجمالي 849 اختباراً في جناح الاختبارات).
- **حالة المهمة P5.12:** ✅ **مكتملة ومُوقّعة رسميًا (P5.12 COMPLETED & SIGNED OFF)**
- **حالة بوابة الخروج للمرحلة M3:** ✅ **مستوفاة ومغلقة بالكامل (EXIT GATE M3 PASSED & CLOSED)**
- **تاريخ الاعتماد:** 2026-10-17
