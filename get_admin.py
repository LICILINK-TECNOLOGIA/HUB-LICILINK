from app import create_app
from app.extensions import db
from app.models.identity import User
from werkzeug.security import generate_password_hash

app = create_app()
with app.app_context():
    admin = User.query.filter_by(is_internal_admin=True).first()
    if admin:
        new_password = "admin_password123"
        admin.password_hash = generate_password_hash(new_password)
        db.session.commit()
        print(f"ADMIN FOUND! Email: {admin.email} | New Password: {new_password}")
    else:
        # Create one just in case
        new_password = "admin_password123"
        new_admin = User(
            name="Admin Test",
            email="admin@licilink.com",
            password_hash=generate_password_hash(new_password),
            is_internal_admin=True
        )
        db.session.add(new_admin)
        db.session.commit()
        print(f"NEW ADMIN CREATED! Email: {new_admin.email} | Password: {new_password}")
