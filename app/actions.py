from __future__ import annotations

import secrets
import smtplib
import time
from dataclasses import dataclass
from email.message import EmailMessage

from .repositories import RepositoryManager


@dataclass
class PendingAction:
    id: str
    session_id: str
    kind: str
    arguments: dict
    description: str
    created_at: float


class ActionStore:
    def __init__(self):
        self.items: dict[str, PendingAction] = {}

    def queue(self, session_id: str, kind: str, arguments: dict, description: str) -> PendingAction:
        action = PendingAction(secrets.token_urlsafe(18), session_id, kind, arguments, description, time.time())
        self.items[action.id] = action
        return action

    def pop(self, action_id: str, session_id: str) -> PendingAction:
        action = self.items.get(action_id)
        if not action or action.session_id != session_id:
            raise ValueError("Unknown or expired action")
        if time.time() - action.created_at > 1800:
            self.items.pop(action_id, None)
            raise ValueError("Action expired")
        return self.items.pop(action_id)


def execute_action(action: PendingAction, repos: RepositoryManager, sender: str, password: str) -> str:
    args = action.arguments
    if action.kind == "apply_patch":
        return repos.apply_patch(args["project_id"], args["patch"])
    if action.kind == "run_command":
        return repos.run_allowed(args["project_id"], list(args["argv"]))
    if action.kind == "send_email":
        if not sender or not password:
            raise ValueError("Email credentials are not configured")
        message = EmailMessage()
        message["From"] = sender
        message["To"] = args["to"]
        message["Subject"] = args["subject"]
        message.set_content(args["body"])
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(sender, password)
            smtp.send_message(message)
        return "Email sent successfully."
    raise ValueError("Unsupported action")
