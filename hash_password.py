# Script para generar un hash de contraseña
from werkzeug.security import generate_password_hash
import sys

# Pide al usuario que introduzca la contraseña
password = input("Introduce la contraseña para hashear: ")

# Genera el hash de la contraseña
hashed_password = generate_password_hash(password)

# Imprime el hash generado
print("\n--- Hash de la Contraseña ---")
print(hashed_password)
print("-----------------------------\n")

# Copiar el hash automáticamente al portapapeles (si es posible)
try:
    import pyperclip
    pyperclip.copy(hashed_password)
    print("El hash ha sido copiado automáticamente al portapapeles.")
except ImportError:
    print("Instala 'pyperclip' (pip install pyperclip) para copiar automáticamente al portapapeles.")
    print("Por favor, copia el hash manualmente.")

# Mantener la ventana abierta hasta que el usuario presione Enter
input("Presiona Enter para salir...")
