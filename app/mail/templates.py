"""Transactional email templates for account invitations and password resets.

Arabic copy strictly complying with project instructions (RTL / Arabic transactional emails).
"""


def render_invitation_email(
    invite_url: str,
    role: str,
    site_name: str | None = None,
) -> tuple[str, str, str]:
    """Render Arabic transactional email for user account invitations.

    Returns:
        tuple[subject, body_text, body_html]
    """
    role_ar = "مشرف موقع" if role == "supervisor" else "مسؤول نظام (أدمن)"
    site_info_text = f"الموقع المخصص: {site_name}\n" if site_name else ""
    site_info_html = f"<p><strong>الموقع المخصص:</strong> {site_name}</p>" if site_name else ""

    subject = "دعوة للانضمام إلى منصة يوم التبرع بالدم"

    body_text = (
        f"مرحبًا،\n\n"
        f"تمت دعوتك للانضمام إلى منصة يوم التبرع بالدم بصفتك ({role_ar}).\n"
        f"{site_info_text}\n"
        f"يرجى الضغط على الرابط التالي لتفعيل حسابك وتعيين كلمة المرور:\n"
        f"{invite_url}\n\n"
        f"ملاحظة: هذا الرابط صالح لمدة 72 ساعة فقط ويُستخدم لمرة واحدة.\n\n"
        f"مع تحيات،\n"
        f"فريق منصة يوم التبرع بالدم"
    )

    body_html = (
        f"<div dir='rtl' style='font-family: sans-serif; line-height: 1.6; color: #333;'>"
        f"<h2>دعوة للانضمام إلى منصة يوم التبرع بالدم</h2>"
        f"<p>مرحبًا،</p>"
        f"<p>تمت دعوتك للانضمام إلى منصة يوم التبرع بالدم بصفتك <strong>({role_ar})</strong>.</p>"
        f"{site_info_html}"
        f"<p>يرجى الضغط على الزر أدناه لتفعيل حسابك واختيار كلمة المرور:</p>"
        f"<p><a href='{invite_url}' style='background-color: #c53030; color: white; padding: 10px 20px; "
        f"text-decoration: none; border-radius: 4px; display: inline-block;'>تفعيل الحساب</a></p>"
        f"<p>أو نسخ الرابط التالي ولصقه في المتصفح:<br><code>{invite_url}</code></p>"
        f"<p style='color: #718096; font-size: 0.9em;'>ملاحظة: هذا الرابط صالح لمدة 72 ساعة فقط ويُستخدم لمرة واحدة.</p>"
        f"<hr style='border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;'>"
        f"<p style='color: #a0aec0; font-size: 0.85em;'>مع تحيات فريق منصة يوم التبرع بالدم</p>"
        f"</div>"
    )

    return subject, body_text, body_html


def render_password_reset_email(reset_url: str) -> tuple[str, str, str]:
    """Render Arabic transactional email for password reset requests.

    Returns:
        tuple[subject, body_text, body_html]
    """
    subject = "إعادة تعيين كلمة المرور - منصة يوم التبرع بالدم"

    body_text = (
        f"مرحبًا،\n\n"
        f"لقد تلقينا طلبًا لإعادة تعيين كلمة المرور الخاصة بحسابك في منصة يوم التبرع بالدم.\n\n"
        f"يرجى الضغط على الرابط التالي لتعيين كلمة مرور جديدة:\n"
        f"{reset_url}\n\n"
        f"ملاحظة: هذا الرابط صالح لمدة ساعة واحدة فقط ويُستخدم لمرة واحدة.\n"
        f"إذا لم تطلب إعادة تعيين كلمة المرور، يمكنك تجاهل هذه الرسالة بأمان دون أي إجراء.\n\n"
        f"مع تحيات،\n"
        f"فريق منصة يوم التبرع بالدم"
    )

    body_html = (
        f"<div dir='rtl' style='font-family: sans-serif; line-height: 1.6; color: #333;'>"
        f"<h2>إعادة تعيين كلمة المرور</h2>"
        f"<p>مرحبًا،</p>"
        f"<p>لقد تلقينا طلبًا لإعادة تعيين كلمة المرور الخاصة بحسابك في منصة يوم التبرع بالدم.</p>"
        f"<p>يرجى الضغط على الزر أدناه لتعيين كلمة مرور جديدة:</p>"
        f"<p><a href='{reset_url}' style='background-color: #c53030; color: white; padding: 10px 20px; "
        f"text-decoration: none; border-radius: 4px; display: inline-block;'>تعيين كلمة مرور جديدة</a></p>"
        f"<p>أو نسخ الرابط التالي ولصقه في المتصفح:<br><code>{reset_url}</code></p>"
        f"<p style='color: #718096; font-size: 0.9em;'>ملاحظة: هذا الرابط صالح لمدة ساعة واحدة فقط ويُستخدم لمرة واحدة.</p>"
        f"<p style='color: #718096; font-size: 0.9em;'>إذا لم تطلب إعادة تعيين كلمة المرور، يمكنك تجاهل هذه الرسالة بأمان دون أي إجراء.</p>"
        f"<hr style='border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;'>"
        f"<p style='color: #a0aec0; font-size: 0.85em;'>مع تحيات فريق منصة يوم التبرع بالدم</p>"
        f"</div>"
    )

    return subject, body_text, body_html
