import os
from pathlib import Path
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Prompt, User, UserRole
from app.auth import hash_password


def main():
    admin_user = os.getenv("DASHBOARD_ADMIN_USER", "admin")
    admin_pass = os.getenv("DASHBOARD_ADMIN_PASS", "admin123")
    prompt_file = Path("/app/prompts/recruiter_v1.md")
    prompt_text = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else ""

    db: Session = SessionLocal()
    try:
        if not db.query(User).filter(User.username == admin_user).first():
            db.add(User(username=admin_user, password_hash=hash_password(admin_pass), role=UserRole.admin))
            print(f"[init] default admin created: {admin_user}/{admin_pass}")
        if not db.query(Prompt).filter(Prompt.version == "v1").first():
            db.query(Prompt).update({"is_active": False})
            db.add(Prompt(name="beibei", version="v1", content=prompt_text, is_active=True, published_by="system"))
            print("[init] prompt v1 inserted")
        db.commit()
    finally:
        db.close()


if __name__ == '__main__':
    main()
