# init_db.py
import os
# Importamos el nuevo modelo SubTask
from app import app, db, User, SubTask 

# Este script se ejecutará para crear las tablas y el usuario admin
# cuando la aplicación se despliegue en Render.

with app.app_context():
    print("--- Inicialización de la Base de Datos ---")
    print("Creando/verificando tablas de la base de datos...")
    db.create_all() # Crea todas las tablas definidas en los modelos si no existen
    print("Tablas creadas/verificadas.")

    # Crea el usuario 'admin' por defecto si no existe
    if not User.query.filter_by(username='Arles').first():
        admin_user = User(username='Arles')
        # ¡CAMBIA ESTA CONTRASEÑA! Usa una contraseña segura.
        admin_user.set_password('password123') 
        db.session.add(admin_user)
        db.session.commit()
        print("Usuario 'Arles' creado por defecto.")
    else:
        print("Usuario 'Arles' ya existe.")

print("Inicialización de la base de datos completada.")