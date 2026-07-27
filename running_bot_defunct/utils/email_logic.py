"""
running_bot/utils/email_logic.py

Gmail SMTP email sender — same pattern as zenodo_bot/utils/email_logic.py
and citation_bot/utils/email_logic.py.

Reads EMAIL_SENDER, EMAIL_RECEIVER, EMAIL_PASSWORD from environment.
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def send_email(subject: str, html_content: str) -> None:
    """
    Send an HTML email via Gmail SMTP.

    Args:
        subject:      Email subject line
        html_content: Full HTML string for the email body
    """
    sender   = os.environ["EMAIL_SENDER"]
    receiver = os.environ["EMAIL_RECEIVER"]
    password = os.environ["EMAIL_PASSWORD"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = receiver

    msg.attach(MIMEText(html_content, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, receiver, msg.as_string())
