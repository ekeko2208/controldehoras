from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime, timedelta
from fpdf import FPDF
import io
import math
from functools import wraps # Importar wraps para el decorador login_required

# --- Configuración de la aplicación Flask ---
app = Flask(__name__)

# Clave secreta (¡IMPORTANTE! Cambiar por una clave más segura en producción)
# Se intenta obtener de una variable de entorno para producción, si no, usa una por defecto.
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'una_clave_secreta_muy_larga_y_aleatoria_para_sesiones_seguras_2024')

# Configuración de la base de datos
# DATABASE_URL será proporcionada por Render en producción.
# Para desarrollo local, usa tus credenciales de PostgreSQL.
# Ejemplo: 'postgresql://usuario_db:contraseña_db@localhost:5432/nombre_db'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'postgresql://tu_usuario_db:tu_contraseña_db@localhost:5432/control_horas_db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False # Desactivar seguimiento de modificaciones, ahorra memoria

# Configuración de sesiones (basadas en el sistema de archivos)
app.config['SESSION_TYPE'] = 'filesystem'
# Duración de la sesión permanente (ej. 30 minutos)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)
Session(app)

# Inicializar SQLAlchemy para la base de datos
db = SQLAlchemy(app)

# --- Definición de Modelos de Base de Datos ---

# Modelo para la tabla de Usuarios
class User(db.Model):
    # Nombre de la tabla en la base de datos (opcional, por defecto es el nombre de la clase en minúsculas)
    __tablename__ = 'users' 
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    # Aumentado el tamaño del String para el hash de la contraseña (de 128 a 255 o 512)
    password_hash = db.Column(db.String(255), nullable=False) # <--- ¡CORREGIDO AQUÍ!
    # Relación uno-a-muchos: un usuario puede tener muchos servicios
    # 'Service' es el nombre del modelo relacionado
    # 'backref='user'' crea un atributo 'user' en el modelo Service para acceder al usuario
    # 'lazy=True' carga los servicios solo cuando se acceden
    services = db.relationship('Service', backref='user', lazy=True)

    # Método para hashear la contraseña antes de guardarla
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    # Método para verificar la contraseña hasheada
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    # Representación de cadena del objeto User (útil para depuración)
    def __repr__(self):
        return f'<User {self.username}>'

# Modelo para la tabla de Servicios
class Service(db.Model):
    # Nombre de la tabla en la base de datos
    __tablename__ = 'services'
    id = db.Column(db.Integer, primary_key=True)
    # Clave foránea que enlaza el servicio a un usuario
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    place = db.Column(db.String(120), nullable=False)
    date = db.Column(db.Date, nullable=False) # Tipo de dato de fecha
    entry_time = db.Column(db.Time, nullable=False) # Tipo de dato de hora
    break_duration = db.Column(db.Integer, nullable=False) # Duración del break en minutos
    exit_time = db.Column(db.Time, nullable=False) # Tipo de dato de hora
    worked_hours = db.Column(db.Float, nullable=False) # Horas trabajadas con decimales
    observations = db.Column(db.Text, nullable=True) # Campo de texto para observaciones (puede ser nulo)

    # Representación de cadena del objeto Service
    def __repr__(self):
        return f'<Service {self.id} - {self.date} - {self.place}>'

# --- Lógica de la Aplicación ---

# Decorador para proteger rutas que requieren inicio de sesión
def login_required(f):
    @wraps(f) # Usa wraps para preservar los metadatos de la función original
    def decorated_function(*args, **kwargs):
        # Verificar si el usuario ha iniciado sesión
        if 'logged_in' not in session or not session['logged_in']:
            flash('Debes iniciar sesión para acceder a esta página.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Context processor para hacer user, month, datetime y today_date disponibles en todas las plantillas Jinja2
@app.context_processor
def inject_global_template_vars():
    current_username = session.get('username')
    current_month = session.get('current_month_selected', datetime.now().strftime("%Y-%m"))
    today_date_str = datetime.now().strftime("%Y-%m-%d") # Formato para input type="date"

    # Lista de nombres de meses en español (en minúsculas para luego capitalizar en la plantilla)
    spanish_month_names = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]
    
    return dict(
        current_username=current_username,
        current_month=current_month,
        today_date=today_date_str, # Para el input de fecha en añadir servicio
        datetime=datetime, # ¡Esto hace que datetime.now() esté disponible en Jinja!
        spanish_month_names=spanish_month_names # Para mostrar meses en español
    )

# --- Funciones Auxiliares ---

# Función para calcular las horas trabajadas
def calculate_worked_hours(entry_time_str, exit_time_str, break_duration_min):
    try:
        # Convertir strings de tiempo a objetos datetime para el cálculo
        entry = datetime.strptime(entry_time_str, '%H:%M')
        exit = datetime.strptime(exit_time_str, '%H:%M')
        
        # Calcular la duración total en minutos
        total_minutes = (exit - entry).total_seconds() / 60
        
        # Restar la duración del break
        worked_minutes = total_minutes - break_duration_min
        
        # Convertir a horas con dos decimales
        worked_hours = round(worked_minutes / 60, 2)
        return worked_hours
    except ValueError:
        # Si hay un error en el formato de tiempo, retornar 0.0
        return 0.0 

# Función para generar el informe PDF
def generate_pdf_report(user_id, services_data, selected_month_str):
    # Configurar el PDF en milímetros, tamaño A4 y orientación HORIZONTAL ('L')
    pdf = FPDF(unit="mm", format="A4", orientation='L') 
    pdf.add_page() # Añadir una página
    pdf.set_auto_page_break(auto=True, margin=15) # Configurar salto de página automático con margen inferior

    # Obtener el nombre del mes en español
    month_num = int(selected_month_str.split('-')[1])
    year_num = selected_month_str.split('-')[0]
    # Usamos la lista de nombres de meses desde el context processor (que es global)
    # Para acceder a ella aquí, necesitamos recrearla o pasarla como argumento,
    # o simplemente definirla de nuevo ya que es una constante.
    spanish_month_names_local = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]
    month_name_spanish = spanish_month_names_local[month_num - 1].capitalize()

    # Título del informe (MODIFICADO)
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, f"Reporte de horas trabajadas para el mes de {month_name_spanish} {year_num}", 0, 1, "C") 
    
    # ELIMINADO: Ya no se muestra el nombre del usuario en el PDF
    # user = User.query.get(user_id)
    # if user:
    #     pdf.set_font("Arial", "B", 12)
    #     pdf.cell(0, 10, f"Usuario: {user.username}", 0, 1, "C")
    pdf.ln(10) # Salto de línea

    # Definir anchos de columna para la tabla del PDF (AJUSTADOS PARA ORIENTACIÓN HORIZONTAL)
    pdf.set_font("Arial", "B", 8) # Fuente para las cabeceras de la tabla
    col_widths = {
        "Lugar": 40,        # Aumentado
        "Fecha": 25,        # Aumentado
        "Entrada": 20,      # Aumentado
        "Break": 20,        # Aumentado
        "Salida": 20,       # Aumentado
        "Horas": 20,        # Aumentado
        "Observaciones": 100 # Aumentado significativamente
    }
    
    # Calcular margen izquierdo para centrar la tabla
    total_table_width = sum(col_widths.values())
    left_margin = (pdf.w - total_table_width) / 2 # pdf.w será el ancho de la página horizontal (297mm)
    
    pdf.set_x(left_margin) # Posicionar el cursor en el margen izquierdo
    # Dibujar cabeceras de la tabla
    for header, width in col_widths.items():
        pdf.cell(width, 7, header, 1, 0, "C") # Celda con borde, sin salto de línea
    pdf.ln() # Salto de línea después de las cabeceras

    # Dibujar datos de la tabla
    pdf.set_font("Arial", "", 7) # Fuente para los datos de la tabla
    total_hours_month = 0.0 # Contador de horas totales

    for service in services_data:
        # Formatear la fecha y hora para la visualización en el PDF
        date_display = service.date.strftime("%d/%m/%Y")
        entry_time_display = service.entry_time.strftime("%H:%M")
        exit_time_display = service.exit_time.strftime("%H:%M")

        # Truncar observaciones si son demasiado largas para una sola línea en la celda
        obs_display = service.observations if service.observations else ""
        # Estimación simple de si el texto es demasiado largo para el ancho de la columna
        # Multiplicamos por 0.8 para dar un poco de margen y evitar que se corte el texto
        if len(obs_display) > (col_widths["Observaciones"] / pdf.get_string_width('A') * 0.8): 
            obs_display = obs_display[:int(col_widths["Observaciones"] / pdf.get_string_width('A') * 0.7)] + "..."
        
        pdf.set_x(left_margin) # Volver al inicio de la fila
        # Dibujar cada celda de la fila
        pdf.cell(col_widths["Lugar"], 6, service.place, 1, 0, "L")
        pdf.cell(col_widths["Fecha"], 6, date_display, 1, 0, "C")
        pdf.cell(col_widths["Entrada"], 6, entry_time_display, 1, 0, "C")
        pdf.cell(col_widths["Break"], 6, str(service.break_duration), 1, 0, "C")
        pdf.cell(col_widths["Salida"], 6, exit_time_display, 1, 0, "C")
        pdf.cell(col_widths["Horas"], 6, f"{service.worked_hours:.2f}", 1, 0, "C") # Formatear horas a 2 decimales
        pdf.cell(col_widths["Observaciones"], 6, obs_display, 1, 1, "L") # Última celda con salto de línea
        
        total_hours_month += service.worked_hours

    pdf.ln(5) # Salto de línea
    pdf.set_font("Arial", "B", 10) # Fuente para el total
    pdf.cell(0, 10, f"Total de horas trabajadas en el mes: {total_hours_month:.2f} horas", 0, 1, "R") # Celda alineada a la derecha

    # Retornar el PDF como bytes
    return pdf.output(dest='S').encode('latin-1')


# --- Rutas de la Aplicación ---

# Ruta para el inicio de sesión
@app.route('/login', methods=['GET', 'POST'])
def login():
    # Si el usuario ya está logueado, redirigir a la página principal
    if 'logged_in' in session and session['logged_in']:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        # Buscar usuario en la base de datos
        user = User.query.filter_by(username=username).first()

        # Verificar credenciales
        if user and user.check_password(password):
            session['logged_in'] = True
            session['username'] = username
            session['user_id'] = user.id # Guardar el ID del usuario en la sesión
            session['current_month_selected'] = datetime.now().strftime("%Y-%m")
            session.permanent = True # Hacer la sesión permanente (sujeta a PERMANENT_SESSION_LIFETIME)
            flash('Has iniciado sesión correctamente.', 'success')
            return redirect(url_for('index'))
        else:
            flash('Usuario o contraseña incorrectos.', 'danger')
    return render_template('login.html')

# Ruta para cerrar sesión
@app.route('/logout')
def logout():
    # Eliminar todas las variables de sesión
    session.pop('logged_in', None)
    session.pop('username', None)
    session.pop('user_id', None)
    session.pop('current_month_selected', None)
    flash('Has cerrado sesión.', 'info')
    return redirect(url_for('login'))

# Ruta principal (requiere inicio de sesión)
@app.route('/', methods=['GET'])
@login_required
def index():
    user_id = session.get('user_id')
    selected_month_str = session.get('current_month_selected')
    
    services = [] # Inicializar lista de servicios vacía
    try:
        # Convertir la cadena del mes a un objeto datetime para la consulta
        selected_month_dt = datetime.strptime(selected_month_str, "%Y-%m")
        start_date = selected_month_dt.replace(day=1).date() # Primer día del mes
        
        # Calcular el último día del mes
        if selected_month_dt.month == 12:
            end_date = selected_month_dt.replace(year=selected_month_dt.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_date = selected_month_dt.replace(month=selected_month_dt.month + 1, day=1) - timedelta(days=1)
        end_date = end_date.date() # Convertir a objeto date

        # Consultar los servicios de la base de datos para el usuario y el mes seleccionados
        # Ordenar por fecha ascendente
        services = Service.query.filter(
            Service.user_id == user_id,
            Service.date >= start_date,
            Service.date <= end_date
        ).order_by(Service.date).all()
        
    except ValueError:
        flash("Formato de mes seleccionado inválido.", "danger")
        services = [] # Vaciar servicios si el formato es inválido

    # Formatear la fecha para mostrar en la tabla (DD/MM/AAAA) en la plantilla
    for service in services:
        service.date_display = service.date.strftime("%d/%m/%Y")

    return render_template('index.html', services=services, selected_month_str=selected_month_str)

# Ruta para añadir un nuevo servicio
@app.route('/add_service', methods=['GET', 'POST'])
@login_required
def add_service():
    if request.method == 'POST':
        user_id = session.get('user_id')
        place = request.form['place']
        date_str = request.form['date']
        entry_time_str = request.form['entry_time']
        break_duration_str = request.form['break_duration']
        exit_time_str = request.form['exit_time']
        observations = request.form.get('observations', '').strip() # .get para evitar KeyError si el campo no existe

        try:
            # Convertir strings a los tipos de datos correctos
            date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
            entry_time_obj = datetime.strptime(entry_time_str, '%H:%M').time()
            exit_time_obj = datetime.strptime(exit_time_str, '%H:%M').time()
            break_duration_min = int(break_duration_str)
            
            worked_hours = calculate_worked_hours(entry_time_str, exit_time_str, break_duration_min)
            
            new_service = Service(
                user_id=user_id,
                place=place,
                date=date_obj,
                entry_time=entry_time_obj,
                break_duration=break_duration_min,
                exit_time=exit_time_obj,
                worked_hours=worked_hours,
                observations=observations
            )
            db.session.add(new_service)
            db.session.commit() # Guardar los cambios en la base de datos
            flash('Servicio añadido correctamente.', 'success')
            return redirect(url_for('index'))
        except ValueError as e:
            flash(f'Error en el formato de los datos: {e}', 'danger')
        except Exception as e:
            db.session.rollback() # Deshacer la transacción si hay un error
            flash(f'Error al añadir servicio: {e}', 'danger')

    # Si la petición es GET o si hubo un error en POST, renderiza el formulario de añadir servicio
    return render_template('add_service.html') # Asume que tienes un add_service.html

# Ruta para editar un servicio existente
@app.route('/edit_service/<int:service_id>', methods=['GET', 'POST'])
@login_required
def edit_service(service_id):
    # Buscar el servicio por ID y por user_id para asegurar que el usuario es el propietario
    # .first_or_404() lanzará un error 404 si el servicio no se encuentra o no pertenece al usuario
    service = Service.query.filter_by(id=service_id, user_id=session.get('user_id')).first_or_404()

    if request.method == 'POST':
        try:
            # Actualizar los atributos del servicio con los datos del formulario
            service.place = request.form['place']
            service.date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
            service.entry_time = datetime.strptime(request.form['entry_time'], '%H:%M').time()
            service.break_duration = int(request.form['break_duration'])
            service.exit_time = datetime.strptime(request.form['exit_time'], '%H:%M').time()
            service.observations = request.form.get('observations', '').strip()

            # Recalcular las horas trabajadas
            service.worked_hours = calculate_worked_hours(
                service.entry_time.strftime('%H:%M'),
                service.exit_time.strftime('%H:%M'),
                service.break_duration
            )

            db.session.commit() # Guardar los cambios en la base de datos
            flash('Servicio actualizado correctamente.', 'success')
            return redirect(url_for('index'))
        except ValueError as e:
            flash(f'Error en el formato de los datos: {e}', 'danger')
        except Exception as e:
            db.session.rollback() # Deshacer la transacción si hay un error
            flash(f'Error al actualizar servicio: {e}', 'danger')
            
    # Para la petición GET, formatear la fecha y hora para que los inputs HTML los muestren correctamente
    service.date_str = service.date.strftime('%Y-%m-%d')
    service.entry_time_str = service.entry_time.strftime('%H:%M')
    service.exit_time_str = service.exit_time.strftime('%H:%M')
    
    return render_template('edit_service.html', service=service)

# Ruta para eliminar un servicio
@app.route('/delete_service/<int:service_id>', methods=['POST'])
@login_required
def delete_service(service_id):
    # Buscar el servicio por ID y user_id para asegurar la propiedad antes de eliminar
    service = Service.query.filter_by(id=service_id, user_id=session.get('user_id')).first_or_404()
    try:
        db.session.delete(service) # Mark the service for deletion
        db.session.commit() # Confirm deletion in the database
        flash('Servicio eliminado correctamente.', 'success')
    except Exception as e:
        db.session.rollback() # Rollback if there's an error
        flash(f'Error al eliminar servicio: {e}', 'danger')
    return redirect(url_for('index'))

# Ruta para cargar servicios de un mes diferente
@app.route('/load_month', methods=['POST'])
@login_required
def load_month():
    selected_month = request.form.get('selected_month')
    if selected_month:
        session['current_month_selected'] = selected_month
        flash(f'Mes cambiado a {selected_month}.', 'info')
    else:
        flash('No se seleccionó ningún mes.', 'warning')
    return redirect(url_for('index'))

# Ruta para descargar el informe PDF
@app.route('/download_pdf')
@login_required
def download_pdf():
    user_id = session.get('user_id')
    selected_month_str = session.get('current_month_selected')

    try:
        # Preparar fechas de inicio y fin del mes para la consulta
        selected_month_dt = datetime.strptime(selected_month_str, "%Y-%m")
        start_date = selected_month_dt.replace(day=1).date()
        if selected_month_dt.month == 12:
            end_date = selected_month_dt.replace(year=selected_month_dt.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_date = selected_month_dt.replace(month=selected_month_dt.month + 1, day=1) - timedelta(days=1)
        end_date = end_date.date()
        
        # Obtener los servicios del usuario para el mes seleccionado
        services = Service.query.filter(
            Service.user_id == user_id,
            Service.date >= start_date,
            Service.date <= end_date
        ).order_by(Service.date).all()
        
        # Generar el PDF y enviarlo como archivo
        pdf_output = generate_pdf_report(user_id, services, selected_month_str)

        return send_file(
            io.BytesIO(pdf_output),
            mimetype='application/pdf',
            as_attachment=True, # Forzar la descarga
            download_name=f'informe_horas_{session["username"]}_{selected_month_str}.pdf'
        )
    except Exception as e:
        flash(f"Error al generar el PDF: {e}", "danger")
        return redirect(url_for('index'))

# --- Ejecutar la aplicación ---
if __name__ == '__main__':
    # Este bloque solo ejecuta el servidor de desarrollo de Flask.
    # La inicialización de la base de datos (creación de tablas y usuario admin)
    # ahora se maneja en un script separado (init_db.py) para el despliegue en Render.
    # Si necesitas inicializar la DB localmente, puedes ejecutar init_db.py directamente.
    app.run(debug=True)