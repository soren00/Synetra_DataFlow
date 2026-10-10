"""告警通知邮件（任务 8.3 失败告警 / 9.5 阈值告警）。

SMTP 未配置时优雅降级：不抛异常，返回 disabled 说明（本地部署无外发邮箱也可完成语义验证）。
"""

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText

from app.core.config import Settings, get_settings


def send_mail(subject: str, body: str, *, to: str | None = None,
              settings: Settings | None = None) -> dict:
    """发送一封邮件。未配置 SMTP 返回降级说明；配置则真实发送。"""
    st = settings or get_settings()
    recipients = to or st.alert_email_to
    if not st.smtp_host or not recipients:
        return {"sent": False, "disabled": True,
                "reason": "SMTP 未配置或未指定收件人", "subject": subject}
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = st.smtp_from
    msg["To"] = recipients
    try:
        with smtplib.SMTP(st.smtp_host, st.smtp_port, timeout=15) as server:
            if st.smtp_user:
                server.starttls()
                server.login(st.smtp_user, st.smtp_password)
            server.send_message(msg)
        return {"sent": True, "to": recipients, "subject": subject}
    except Exception as e:  # noqa: BLE001 - 转为可读说明，不阻塞流程
        return {"sent": False, "reason": f"邮件发送失败: {e}", "subject": subject}


def send_failure_alert(task_key: str, project: str, error: str, *, to: str | None = None,
                       settings: Settings | None = None) -> dict:
    """8.3 任务最终失败后的邮件告警。"""
    st = settings or get_settings()
    return send_mail(
        f"[Synetra] 任务失败: {task_key}",
        f"任务 {task_key}（项目 {project}）执行失败。\n失败原因: {error}",
        to=to, settings=st,
    )


__all__ = ["send_failure_alert", "send_mail"]