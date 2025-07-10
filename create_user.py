# create_user.py
import os
from app import app, db, User # Importamos app, db y el modelo User de tu app.py
from getpass import getpass # Para pedir la contraseña de forma segura

# Este script se ejecutará para crear nuevos usuarios en la base de datos.

with app.app_context():
    print("--- Crear Nuevo Usuario ---")

    username = input("Introduce el nombre de usuario para el nuevo usuario: ").strip()
    
    if not username:
        print("El nombre de usuario no puede estar vacío. Abortando.")
    else:
        # Comprobar si el usuario ya existe
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            print(f"Error: El usuario '{username}' ya existe. Por favor, elige otro nombre.")
        else:
            password = getpass("Introduce la contraseña para el nuevo usuario: ")
            confirm_password = getpass("Confirma la contraseña: ")

            if password != confirm_password:
                print("Las contraseñas no coinciden. Abortando.")
            elif not password:
                print("La contraseña no puede estar vacía. Abortando.")
            else:
                try:
                    new_user = User(username=username)
                    new_user.set_password(password) # Hashear y guardar la contraseña
                    db.session.add(new_user)
                    db.session.commit() # Guardar el nuevo usuario en la base de datos
                    print(f"Usuario '{username}' creado exitosamente.")
                except Exception as e:
                    db.session.rollback() # Deshacer la transacción si hay un error
                    print(f"Error al crear el usuario: {e}")

print("Proceso de creación de usuario finalizado.")