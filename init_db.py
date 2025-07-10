# init_db.py
import os
from app import app, db, User # Importamos app, db y el modelo User de tu app.py

# Este script se ejecutará para crear las tablas y el usuario admin
# cuando la aplicación se despliegue en Render.

with app.app_context():
    print("Creando tablas de la base de datos...")
    db.create_all() # Crea todas las tablas definidas en los modelos si no existen
    print("Tablas creadas/verificadas.")

    # Crea el usuario 'admin' por defecto si no existe
    if not User.query.filter_by(username='admin').first():
        admin_user = User(username='admin')
        # ¡CAMBIA ESTA CONTRASEÑA! Usa una contraseña segura.
        # En producción, considera usar una variable de entorno para esto también.
        admin_user.set_password('password123') 
        db.session.add(admin_user)
        db.session.commit()
        print("Usuario 'admin' creado por defecto.")
    else:
        print("Usuario 'admin' ya existe.")

print("Inicialización de la base de datos completada.")