# init_db.py
from app import app, db, User # Solo importamos User, ya no SubTask ni Service aquí
import os

# Define la contraseña predeterminada para el usuario 'admin'
# ¡IMPORTANTE! En un entorno de producción, esta contraseña DEBE ser cambiada
# y no debe estar hardcodeada aquí. Considera usar variables de entorno.
DEFAULT_ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'password123') 

# Crea el contexto de la aplicación para que SQLAlchemy pueda interactuar con la base de datos
with app.app_context():
    # Crea todas las tablas definidas en los modelos (User y Service) si no existen
    db.create_all()

    # Crea el usuario 'admin' por defecto si no existe en la base de datos
    if not User.query.filter_by(username='Arles').first():
        admin_user = User(username='Arles')
        admin_user.set_password(DEFAULT_ADMIN_PASSWORD)
        db.session.add(admin_user)
        db.session.commit()
        print("Usuario 'Arles' creado por defecto con contraseña:", DEFAULT_ADMIN_PASSWORD)
    else:
        print("El usuario 'Arles' ya existe.")

    print("Base de datos inicializada correctamente.")