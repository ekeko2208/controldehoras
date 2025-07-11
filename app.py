from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime, timedelta
from fpdf import FPDF
import io
import math
from functools import wraps
from collections import defaultdict # Para agrupar tareas

# --- Configuración de la aplicación Flask ---
app = Flask(__name__)

# Clave secreta (¡IMPORTANTE! Cambiar por una clave más segura en producción)
# Se intenta obtener de una variable de entorno para producción, si no, usa una por defecto.
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'una_clave_secreta_muy_larga_y_aleatoria_para_sesiones_seguras_2024')

# Configuración de la base de datos
# DATABASE_URL será proporcionada por Render en producción.
# Para desarrollo local, usa tus credenciales de PostgreSQL.
# Ejemplo: 'postgresql://tu_usuario_db:tu_contraseña_db@localhost:5432/control_horas_db'
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
    password_hash = db.Column(db.String(255), nullable=False) 
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

# Función para generar el informe PDF de Servicios
def generate_pdf_report(user_id, services_data, selected_month_str):
    # Configurar el PDF en milímetros, tamaño A4 y orientación HORIZONTAL ('L')
    pdf = FPDF(unit="mm", format="A4", orientation='L') 
    pdf.add_page() # Añadir una página
    pdf.set_auto_page_break(auto=True, margin=15) # Configurar salto de página automático con margen inferior

    # Obtener el nombre del mes en español
    month_num = int(selected_month_str.split('-')[1])
    year_num = selected_month_str.split('-')[0]
    spanish_month_names_local = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]
    month_name_spanish = spanish_month_names_local[month_num - 1].capitalize()

    # Título del informe
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, f"Reporte de horas trabajadas para el mes de {month_name_spanish} {year_num}", 0, 1, "C") 
    
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

# Función para generar el informe PDF de Tareas (nuevo)
def generate_tasks_pdf_report(tasks_summary_data, selected_month_str):
    pdf = FPDF(unit="mm", format="A4", orientation='P') # Orientación vertical para resumen de tareas
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    month_num = int(selected_month_str.split('-')[1])
    year_num = selected_month_str.split('-')[0]
    spanish_month_names_local = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]
    month_name_spanish = spanish_month_names_local[month_num - 1].capitalize()

    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, f"Resumen de Horas por Tarea/Lugar para {month_name_spanish} {year_num}", 0, 1, "C")
    pdf.ln(10)

    pdf.set_font("Arial", "B", 10)
    col_widths = {"Tarea/Lugar": 120, "Total Horas": 40}
    total_table_width = sum(col_widths.values())
    left_margin = (pdf.w - total_table_width) / 2
    
    pdf.set_x(left_margin)
    for header, width in col_widths.items():
        pdf.cell(width, 7, header, 1, 0, "C")
    pdf.ln()

    pdf.set_font("Arial", "", 9)
    for task, total_hours in tasks_summary_data.items():
        pdf.set_x(left_margin)
        pdf.cell(col_widths["Tarea/Lugar"], 6, task, 1, 0, "L")
        pdf.cell(col_widths["Total Horas"], 6, f"{total_hours:.2f}", 1, 1, "C")

    pdf.ln(5)
    pdf.set_font("Arial", "B", 10)
    total_general_hours = sum(tasks_summary_data.values())
    pdf.cell(0, 10, f"Total General de Horas: {total_general_hours:.2f} horas", 0, 1, "R")

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
    total_hours_display = "00:00" # Inicializar total de horas
    search_query = request.args.get('search', '').strip() # Obtener el término de búsqueda

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
        services_query = Service.query.filter(
            Service.user_id == user_id,
            Service.date >= start_date,
            Service.date <= end_date
        )

        # Aplicar filtro por descripción si hay una búsqueda.
        if search_query:
            # Buscar en observaciones y/o lugar (puedes ajustar según necesites)
            services_query = services_query.filter(
                (Service.observations.ilike(f'%{search_query}%')) |
                (Service.place.ilike(f'%{search_query}%'))
            )

        services = services_query.order_by(Service.date.desc(), Service.entry_time.desc()).all()
        
        # Calcular el total de horas para el período filtrado.
        total_minutes_overall = sum(s.worked_hours * 60 for s in services)
        total_hours_overall = int(total_minutes_overall // 60)
        total_remaining_minutes = int(total_minutes_overall % 60)
        total_hours_display = f"{total_hours_overall:02d}:{total_remaining_minutes:02d}"


    except ValueError:
        flash("Formato de mes seleccionado inválido.", "danger")
        services = [] # Vaciar servicios si el formato es inválido
        total_hours_display = "00:00"
    except Exception as e:
        flash(f"Ocurrió un error al cargar los servicios: {e}", "danger")
        services = []
        total_hours_display = "00:00"

    # Formatear la fecha para mostrar en la tabla (DD/MM/AAAA) en la plantilla
    for service in services:
        service.date_display = service.date.strftime("%d/%m/%Y")

    return render_template('index.html', 
                           services=services, 
                           total_hours_display=total_hours_display,
                           search_query=search_query) # Pasar search_query a la plantilla

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
            
            # Si el checkbox "no_discount_break" está marcado, el break_duration es 0
            no_discount_break = 'no_discount_break' in request.form
            break_duration_min = 0 if no_discount_break else int(break_duration_str)
            
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
            service.place = request.form['place']
            service.date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
            service.entry_time = datetime.strptime(request.form['entry_time'], '%H:%M').time()
            service.exit_time = datetime.strptime(request.form['exit_time'], '%H:%M').time()
            service.observations = request.form.get('observations', '').strip()

            # Si el checkbox "no_discount_break" está marcado, el break_duration es 0
            no_discount_break = 'no_discount_break' in request.form
            service.break_duration = 0 if no_discount_break else int(request.form['break_duration'])

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

# Ruta para cargar servicios de un mes diferente (para index.html)
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

# Ruta para cargar el mes en el resumen de tareas (para tasks.html)
@app.route('/load_tasks_month', methods=['POST'])
@login_required
def load_tasks_month():
    selected_month = request.form.get('selected_month')
    if selected_month:
        session['current_month_selected'] = selected_month
        flash(f'Mes cambiado a {selected_month} para el resumen de tareas.', 'info')
    else:
        flash('No se seleccionó ningún mes.', 'warning')
    return redirect(url_for('tasks_summary'))

# Ruta para descargar el informe PDF de Servicios
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
        
        services = Service.query.filter(
            Service.user_id == user_id,
            Service.date >= start_date,
            Service.date <= end_date
        ).order_by(Service.date).all()
        
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

# NUEVA RUTA: Vista previa del PDF de Servicios
@app.route('/preview_pdf')
@login_required
def preview_pdf():
    user_id = session.get('user_id')
    selected_month_str = session.get('current_month_selected')

    try:
        selected_month_dt = datetime.strptime(selected_month_str, "%Y-%m")
        start_date = selected_month_dt.replace(day=1).date()
        if selected_month_dt.month == 12:
            end_date = selected_month_dt.replace(year=selected_month_dt.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_date = selected_month_dt.replace(month=selected_month_dt.month + 1, day=1) - timedelta(days=1)
        end_date = end_date.date()
        
        services = Service.query.filter(
            Service.user_id == user_id,
            Service.date >= start_date,
            Service.date <= end_date
        ).order_by(Service.date).all()
        
        pdf_output = generate_pdf_report(user_id, services, selected_month_str)

        # Enviar el PDF para que se visualice en el navegador (Content-Disposition: inline)
        return send_file(
            io.BytesIO(pdf_output),
            mimetype='application/pdf',
            as_attachment=False # <--- CAMBIO CLAVE: False para vista previa
        )
    except Exception as e:
        flash(f"Error al generar la vista previa del PDF: {e}", "danger")
        return redirect(url_for('index'))

# NUEVA RUTA: Resumen de Tareas Específicas
@app.route('/tasks_summary', methods=['GET', 'POST'])
@login_required
def tasks_summary():
    user_id = session.get('user_id')
    selected_month_str = session.get('current_month_selected')

    # Si se envía el formulario de selección de mes, actualizar la sesión
    if request.method == 'POST':
        form_selected_month = request.form.get('selected_month')
        if form_selected_month:
            session['current_month_selected'] = form_selected_month
            selected_month_str = form_selected_month # Actualizar para la lógica actual
            flash(f'Mes cambiado a {selected_month_str} para el resumen de tareas.', 'info')
        else:
            flash('No se seleccionó ningún mes para el resumen de tareas.', 'warning')
            
    tasks_summary_data = {}
    try:
        selected_month_dt = datetime.strptime(selected_month_str, "%Y-%m")
        start_date = selected_month_dt.replace(day=1).date()
        if selected_month_dt.month == 12:
            end_date = selected_month_dt.replace(year=selected_month_dt.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_date = selected_month_dt.replace(month=selected_month_dt.month + 1, day=1) - timedelta(days=1)
        end_date = end_date.date()

        # Obtener todos los servicios para el usuario y el mes
        services = Service.query.filter(
            Service.user_id == user_id,
            Service.date >= start_date,
            Service.date <= end_date
        ).all()

        # Agrupar las horas por 'place'
        grouped_hours = defaultdict(float)
        for service in services:
            grouped_hours[service.place] += service.worked_hours
        
        # Convertir defaultdict a un diccionario normal para pasar a la plantilla
        tasks_summary_data = dict(grouped_hours)

    except ValueError:
        flash("Formato de mes seleccionado inválido para el resumen de tareas.", "danger")
    except Exception as e:
        flash(f"Ocurrió un error al cargar el resumen de tareas: {e}", "danger")

    return render_template('tasks.html', tasks_summary=tasks_summary_data)

# NUEVA RUTA: Generar PDF de Tareas
@app.route('/generate_tasks_pdf')
@login_required
def generate_tasks_pdf():
    user_id = session.get('user_id')
    selected_month_str = session.get('current_month_selected')

    tasks_summary_data = {}
    try:
        selected_month_dt = datetime.strptime(selected_month_str, "%Y-%m")
        start_date = selected_month_dt.replace(day=1).date()
        if selected_month_dt.month == 12:
            end_date = selected_month_dt.replace(year=selected_month_dt.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_date = selected_month_dt.replace(month=selected_month_dt.month + 1, day=1) - timedelta(days=1)
        end_date = end_date.date()

        services = Service.query.filter(
            Service.user_id == user_id,
            Service.date >= start_date,
            Service.date <= end_date
        ).all()

        grouped_hours = defaultdict(float)
        for service in services:
            grouped_hours[service.place] += service.worked_hours
        
        tasks_summary_data = dict(grouped_hours)

        pdf_output = generate_tasks_pdf_report(tasks_summary_data, selected_month_str)

        return send_file(
            io.BytesIO(pdf_output),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'resumen_tareas_{session["username"]}_{selected_month_str}.pdf'
        )
    except Exception as e:
        flash(f"Error al generar el PDF de tareas: {e}", "danger")
        return redirect(url_for('tasks_summary'))

# NUEVA RUTA: Gestión de Perfil
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user_id = session.get('user_id')
    user = User.query.get(user_id)

    if request.method == 'POST':
        # Lógica para actualizar nombre de usuario
        if 'username' in request.form:
            new_username = request.form['username'].strip()
            if new_username and new_username != user.username:
                # Verificar si el nuevo nombre de usuario ya existe
                existing_user = User.query.filter_by(username=new_username).first()
                if existing_user and existing_user.id != user.id:
                    flash('El nombre de usuario ya está en uso. Por favor, elige otro.', 'danger')
                else:
                    user.username = new_username
                    session['username'] = new_username # Actualizar la sesión
                    db.session.commit()
                    flash('Nombre de usuario actualizado correctamente.', 'success')
            elif new_username == user.username:
                flash('El nombre de usuario es el mismo que el actual.', 'info')
            else:
                flash('El nombre de usuario no puede estar vacío.', 'danger')

        # Lógica para cambiar contraseña
        if 'current_password' in request.form:
            current_password = request.form['current_password']
            new_password = request.form['new_password']
            confirm_new_password = request.form['confirm_new_password']

            if not user.check_password(current_password):
                flash('La contraseña actual es incorrecta.', 'danger')
            elif not new_password or len(new_password) < 6:
                flash('La nueva contraseña debe tener al menos 6 caracteres.', 'danger')
            elif new_password != confirm_new_password:
                flash('La nueva contraseña y la confirmación no coinciden.', 'danger')
            else:
                user.set_password(new_password)
                db.session.commit()
                flash('Contraseña actualizada correctamente.', 'success')
        
        return redirect(url_for('profile')) # Redirigir siempre después de POST

    return render_template('profile.html', user=user)

# NUEVA RUTA: Olvidé mi contraseña
@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        username = request.form['username'].strip()
        user = User.query.filter_by(username=username).first()

        if user:
            # En un entorno real, aquí generarías un token único y lo enviarías por correo electrónico.
            # Por simplicidad, aquí solo flashearemos un mensaje.
            # Enlace de ejemplo (no funcional sin un sistema de tokens real):
            # token = generate_reset_token(user.id) # Necesitarías implementar esta función
            # reset_link = url_for('reset_password', token=token, _external=True)
            flash(f'Si el usuario existe, se ha enviado un enlace de restablecimiento de contraseña (funcionalidad no implementada en esta demo).', 'info')
        else:
            # Es buena práctica no revelar si el usuario existe o no por seguridad.
            flash('Si el usuario existe, se ha enviado un enlace de restablecimiento de contraseña (funcionalidad no implementada en esta demo).', 'info')
        return redirect(url_for('login'))
    return render_template('forgot_password.html')

# NUEVA RUTA: Restablecer contraseña (con token, aunque el token no se valida en esta demo)
@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    # En un entorno real, aquí validarías el token:
    # user_id = verify_reset_token(token) # Necesitarías implementar esta función
    # if not user_id:
    #     flash('El enlace de restablecimiento es inválido o ha expirado.', 'danger')
    #     return redirect(url_for('forgot_password'))
    
    # Para esta demo, asumimos que el token es válido y lo pasamos al formulario
    # y el usuario puede cambiar la contraseña.
    # En un entorno real, buscarías al usuario por el user_id del token.
    # Aquí, como no hay validación de token real, no podemos buscar al usuario por ID.
    # Esta parte es solo para la demostración del flujo de la UI.
    # La lógica real de actualización de contraseña se haría aquí si el token fuera válido.

    if request.method == 'POST':
        new_password = request.form['new_password']
        confirm_new_password = request.form['confirm_new_password']
        
        # En un entorno real, aquí buscarías al usuario por el user_id del token
        # user = User.query.get(user_id) 
        # Para la demo, no podemos hacer esto sin un token real.
        # Por lo tanto, esta parte es solo un placeholder para el flujo.
        
        if not new_password or len(new_password) < 6:
            flash('La nueva contraseña debe tener al menos 6 caracteres.', 'danger')
        elif new_password != confirm_new_password:
            flash('Las contraseñas no coinciden.', 'danger')
        else:
            # Aquí iría la lógica para actualizar la contraseña del usuario real
            # user.set_password(new_password)
            # db.session.commit()
            flash('Tu contraseña ha sido restablecida correctamente (funcionalidad no implementada en esta demo).', 'success')
            return redirect(url_for('login'))
    
    return render_template('reset_password.html', token=token)


# Ruta para el registro de nuevos usuarios
@app.route('/register', methods=['GET', 'POST'])
def register():
    # Define el código de registro esperado
    REGISTRATION_CODE = "arles2208." # ¡CÓDIGO DE REGISTRO ESPECIAL!

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        registration_code = request.form['registration_code'] # Obtener el código del formulario

        # Validar el código de registro
        if registration_code != REGISTRATION_CODE:
            flash('Código de registro incorrecto.', 'danger')
            return render_template('register.html')

        # Validaciones básicas para el registro.
        if len(username) < 3:
            flash('El nombre de usuario debe tener al menos 3 caracteres.', 'danger')
            return render_template('register.html')
        if len(password) < 6:
            flash('La contraseña debe tener al menos 6 caracteres.', 'danger')
            return render_template('register.html')
        if password != confirm_password:
            flash('Las contraseñas no coinciden.', 'danger')
            return render_template('register.html')
        if User.query.filter_by(username=username).first():
            flash('El nombre de usuario ya existe.', 'danger')
            return render_template('register.html')

        # Crea un nuevo usuario y lo guarda en la base de datos.
        new_user = User(username=username)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        flash('¡Registro exitoso! Ahora puedes iniciar sesión.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')


# --- Ejecutar la aplicación ---
if __name__ == '__main__':
    # Este bloque solo ejecuta el servidor de desarrollo de Flask.
    # La inicialización de la base de datos (creación de tablas y usuario admin)
    # ahora se maneja en un script separado (init_db.py) para el despliegue en Render.
    # Si necesitas inicializar la DB localmente, puedes ejecutar init_db.py directamente.

    # Crea el contexto de aplicación para que db.create_all() pueda acceder a la configuración de Flask
    with app.app_context():
        db.create_all() # Crea todas las tablas definidas en los modelos si no existen

        # Crea el usuario 'admin' por defecto si no existe en la base de datos
        if not User.query.filter_by(username='admin').first():
            admin_user = User(username='admin')
            # ¡CAMBIA ESTA CONTRASEÑA! Usa una contraseña segura y no la dejes en el código fuente.
            admin_user.set_password('password123') 
            db.session.add(admin_user)
            db.session.commit()
            print("Usuario 'admin' creado por defecto.")
            
    # Ejecuta el servidor de desarrollo de Flask
    app.run(debug=True)