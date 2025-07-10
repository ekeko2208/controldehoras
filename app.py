from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime, timedelta
# Cambiado de fpdf a fpdf2
from fpdf2 import FPDF 
import io
import math
from functools import wraps

# --- Configuración de la aplicación Flask ---
app = Flask(__name__)

# Clave secreta (¡IMPORTANTE! Cambiar por una clave más segura en producción)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'una_clave_secreta_muy_larga_y_aleatoria_para_sesiones_seguras_2024')

# Configuración de la base de datos
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'postgresql://tu_usuario_db:tu_contraseña_db@localhost:5432/control_horas_db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False 

# Configuración de sesiones
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)
Session(app)

# Inicializar SQLAlchemy para la base de datos
db = SQLAlchemy(app)

# --- Definición de Modelos de Base de Datos ---

# Modelo para la tabla de Usuarios
class User(db.Model):
    __tablename__ = 'users' 
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False) 
    services = db.relationship('Service', backref='user', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.username}>'

# Modelo para la tabla de Servicios
class Service(db.Model):
    __tablename__ = 'services'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    place = db.Column(db.String(120), nullable=False)
    date = db.Column(db.Date, nullable=False) 
    entry_time = db.Column(db.Time, nullable=False) 
    break_duration = db.Column(db.Integer, nullable=False) # Duración del break en minutos (siempre se guarda el valor)
    exit_time = db.Column(db.Time, nullable=False) 
    worked_hours = db.Column(db.Float, nullable=False) 
    observations = db.Column(db.Text, nullable=True) 

    # Nueva relación uno-a-muchos con SubTask
    # cascade="all, delete-orphan" asegura que las sub-tareas se eliminen si se elimina el servicio
    subtasks = db.relationship('SubTask', backref='service', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Service {self.id} - {self.date} - {self.place}>'

# NUEVO MODELO: SubTask para las tareas detalladas dentro de un servicio
class SubTask(db.Model):
    __tablename__ = 'subtasks'
    id = db.Column(db.Integer, primary_key=True)
    service_id = db.Column(db.Integer, db.ForeignKey('services.id'), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    hours = db.Column(db.Float, nullable=False) # Horas dedicadas a esta sub-tarea

    def __repr__(self):
        return f'<SubTask {self.id} - {self.description} ({self.hours}h)>'


# --- Lógica de la Aplicación ---

# Decorador para proteger rutas que requieren inicio de sesión
def login_required(f):
    @wraps(f) 
    def decorated_function(*args, **kwargs):
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
    today_date_str = datetime.now().strftime("%Y-%m-%d") 

    spanish_month_names = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]
    
    return dict(
        current_username=current_username,
        current_month=current_month,
        today_date=today_date_str, 
        datetime=datetime, 
        spanish_month_names=spanish_month_names 
    )

# --- Funciones Auxiliares ---

# Función para calcular las horas trabajadas
# Ahora acepta un parámetro `discount_break` para controlar si se descuenta el break
def calculate_worked_hours(entry_time_str, exit_time_str, break_duration_min, discount_break=True):
    try:
        entry = datetime.strptime(entry_time_str, '%H:%M')
        exit = datetime.strptime(exit_time_str, '%H:%M')
        
        total_minutes = (exit - entry).total_seconds() / 60
        
        # Solo resta el break si discount_break es True
        if discount_break:
            worked_minutes = total_minutes - break_duration_min
        else:
            worked_minutes = total_minutes # No descontar el break
        
        worked_hours = round(worked_minutes / 60, 2)
        return worked_hours
    except ValueError:
        return 0.0 

# Función para generar el informe PDF (COMPLETAMENTE REFACTORIZADA para fpdf2 y diseño)
def generate_pdf_report(user_id, services_data, selected_month_str):
    pdf = FPDF(unit="mm", format="A4", orientation='L') 
    
    # --- Configuración global para el PDF ---
    pdf.set_auto_page_break(auto=True, margin=15) 
    pdf.set_font("Arial", "", 10) # Fuente por defecto
    
    # Colores
    COLOR_HEADER_BG = (52, 73, 94)  # Azul oscuro (similar al navbar)
    COLOR_HEADER_TEXT = (255, 255, 255) # Blanco
    COLOR_PRIMARY_TEXT = (50, 50, 50) # Gris oscuro para texto general
    COLOR_ACCENT = (52, 152, 219) # Azul vibrante
    COLOR_LIGHT_GRAY = (240, 240, 240) # Gris claro para fondo de filas alternas

    month_num = int(selected_month_str.split('-')[1])
    year_num = selected_month_str.split('-')[0]
    spanish_month_names_local = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]
    month_name_spanish = spanish_month_names_local[month_num - 1].capitalize()

    # --- Página 1: Resumen de Servicios ---
    pdf.add_page() 

    # Título principal
    pdf.set_text_color(*COLOR_PRIMARY_TEXT)
    pdf.set_font("Arial", "B", 20)
    pdf.cell(0, 15, f"Reporte de Horas Trabajadas", 0, 1, "C") 
    pdf.set_font("Arial", "", 14)
    pdf.cell(0, 10, f"Mes: {month_name_spanish} {year_num}", 0, 1, "C")
    pdf.ln(10) 

    # Cabeceras de la tabla principal
    pdf.set_fill_color(*COLOR_HEADER_BG)
    pdf.set_text_color(*COLOR_HEADER_TEXT)
    pdf.set_font("Arial", "B", 9) 
    col_widths_main = {
        "Lugar": 40,
        "Fecha": 25,
        "Entrada": 20,
        "Break": 20,
        "Salida": 20,
        "Horas": 20,
        "Observaciones": 120 
    }
    
    total_table_width_main = sum(col_widths_main.values())
    left_margin_main = (pdf.w - total_table_width_main) / 2 
    
    pdf.set_x(left_margin_main) 
    for header, width in col_widths_main.items():
        pdf.cell(width, 10, header, 1, 0, "C", fill=True) 
    pdf.ln() 

    # Datos de la tabla principal
    pdf.set_text_color(*COLOR_PRIMARY_TEXT)
    pdf.set_font("Arial", "", 8) 
    total_hours_month = 0.0 
    LINE_HEIGHT_MAIN_TABLE = 6 

    for i, service in enumerate(services_data):
        date_display = service.date.strftime("%d/%m/%Y")
        entry_time_display = service.entry_time.strftime("%H:%M")
        exit_time_display = service.exit_time.strftime("%H:%M")
        obs_display = service.observations if service.observations else ""
        
        # --- Calcular altura de la fila para la tabla principal usando dry_run ---
        # Guardar la Y actual antes de la simulación
        initial_y = pdf.get_y()
        # Simular multi_cell para Observaciones para obtener la altura
        pdf.multi_cell(col_widths_main["Observaciones"], LINE_HEIGHT_MAIN_TABLE, obs_display, 0, "L", 0, 0, pdf.get_x() + left_margin_main + sum(col_widths_main[h] for h in ["Lugar", "Fecha", "Entrada", "Break", "Salida", "Horas"]), initial_y, dry_run=True)
        obs_height = pdf.get_y() - initial_y
        pdf.set_y(initial_y) # Restablecer Y después de la simulación

        row_height = max(obs_height, LINE_HEIGHT_MAIN_TABLE + 2) # Mínimo una línea con padding

        # Asegurarse de que no se salga de la página antes de dibujar la fila
        if pdf.get_y() + row_height > pdf.page_break_trigger:
            pdf.add_page()
            # Redibujar cabeceras en la nueva página
            pdf.set_fill_color(*COLOR_HEADER_BG)
            pdf.set_text_color(*COLOR_HEADER_TEXT)
            pdf.set_font("Arial", "B", 9) 
            pdf.set_x(left_margin_main) 
            for header, width in col_widths_main.items():
                pdf.cell(width, 10, header, 1, 0, "C", fill=True) 
            pdf.ln()
            pdf.set_text_color(*COLOR_PRIMARY_TEXT)
            pdf.set_font("Arial", "", 8) 

        # Establecer color de fondo alterno para las filas
        if i % 2 == 0:
            pdf.set_fill_color(255, 255, 255) # Blanco
        else:
            pdf.set_fill_color(*COLOR_LIGHT_GRAY) # Gris claro

        start_y_for_row = pdf.get_y() # Guardar la Y inicial para esta fila
        current_x = left_margin_main

        # Dibujar celdas de una sola línea
        pdf.set_xy(current_x, start_y_for_row)
        pdf.cell(col_widths_main["Lugar"], row_height, service.place, 1, 0, "L", fill=True)
        current_x += col_widths_main["Lugar"]

        pdf.set_xy(current_x, start_y_for_row)
        pdf.cell(col_widths_main["Fecha"], row_height, date_display, 1, 0, "C", fill=True)
        current_x += col_widths_main["Fecha"]

        pdf.set_xy(current_x, start_y_for_row)
        pdf.cell(col_widths_main["Entrada"], row_height, entry_time_display, 1, 0, "C", fill=True)
        current_x += col_widths_main["Entrada"]

        pdf.set_xy(current_x, start_y_for_row)
        pdf.cell(col_widths_main["Break"], row_height, str(service.break_duration), 1, 0, "C", fill=True)
        current_x += col_widths_main["Break"]

        pdf.set_xy(current_x, start_y_for_row)
        pdf.cell(col_widths_main["Salida"], row_height, exit_time_display, 1, 0, "C", fill=True)
        current_x += col_widths_main["Salida"]

        pdf.set_xy(current_x, start_y_for_row)
        pdf.cell(col_widths_main["Horas"], row_height, f"{service.worked_hours:.2f}", 1, 0, "C", fill=True)
        current_x += col_widths_main["Horas"]

        # Dibujar Observaciones (multi_cell)
        pdf.set_xy(current_x, start_y_for_row)
        pdf.multi_cell(col_widths_main["Observaciones"], LINE_HEIGHT_MAIN_TABLE, obs_display, 1, "L", fill=True, ln=1) 
        
        # Aseguramos que la próxima fila comience en la posición correcta
        pdf.set_y(start_y_for_row + row_height)

        total_hours_month += service.worked_hours

    pdf.ln(5) 
    pdf.set_font("Arial", "B", 12) 
    pdf.set_text_color(*COLOR_ACCENT)
    pdf.cell(0, 10, f"Total de horas trabajadas en el mes: {total_hours_month:.2f} horas", 0, 1, "R") 

    # --- Página 2 (o siguientes): Tareas Detalladas ---
    # Solo añadir esta página si hay servicios con subtareas
    has_subtasks = any(service.subtasks for service in services_data)
    if has_subtasks:
        pdf.add_page()
        pdf.set_text_color(*COLOR_PRIMARY_TEXT)
        pdf.set_font("Arial", "B", 20)
        pdf.cell(0, 15, "Detalle de Tareas por Servicio", 0, 1, "C")
        pdf.set_font("Arial", "", 14)
        pdf.cell(0, 10, f"Mes: {month_name_spanish} {year_num}", 0, 1, "C")
        pdf.ln(10)

        # Cabeceras de la tabla de subtareas
        pdf.set_fill_color(*COLOR_HEADER_BG)
        pdf.set_text_color(*COLOR_HEADER_TEXT)
        pdf.set_font("Arial", "B", 9)
        col_widths_subtasks = {
            "Fecha": 25,
            "Lugar": 45,
            "Descripción de Tarea": 150,
            "Horas": 20
        }
        total_table_width_subtasks = sum(col_widths_subtasks.values())
        left_margin_subtasks = (pdf.w - total_table_width_subtasks) / 2

        pdf.set_x(left_margin_subtasks)
        for header, width in col_widths_subtasks.items():
            pdf.cell(width, 10, header, 1, 0, "C", fill=True)
        pdf.ln()

        pdf.set_text_color(*COLOR_PRIMARY_TEXT)
        pdf.set_font("Arial", "", 8)
        LINE_HEIGHT_SUBTASK_TABLE = 6

        for i, service in enumerate(services_data):
            if service.subtasks:
                # Encabezado para cada servicio con sub-tareas
                pdf.set_font("Arial", "B", 9)
                pdf.set_text_color(*COLOR_ACCENT)
                pdf.set_x(left_margin_subtasks)
                pdf.cell(total_table_width_subtasks, 8, f"Servicio: {service.place} - {service.date.strftime('%d/%m/%Y')}", 1, 1, "L", fill=False)
                pdf.set_text_color(*COLOR_PRIMARY_TEXT)
                pdf.set_font("Arial", "", 8) # Restablecer fuente para datos

                for j, subtask in enumerate(service.subtasks):
                    desc_display = subtask.description
                    hours_display = f"{subtask.hours:.1f}"

                    # Calcular altura de la fila para sub-tareas
                    initial_y = pdf.get_y()
                    pdf.multi_cell(col_widths_subtasks["Descripción de Tarea"], LINE_HEIGHT_SUBTASK_TABLE, desc_display, 0, "L", 0, 0, pdf.get_x() + left_margin_subtasks + col_widths_subtasks["Fecha"] + col_widths_subtasks["Lugar"], initial_y, dry_run=True)
                    desc_height = pdf.get_y() - initial_y
                    pdf.set_y(initial_y) # Restablecer Y

                    subtask_row_height = max(desc_height, LINE_HEIGHT_SUBTASK_TABLE + 2) # Añadir padding

                    # Asegurarse de que no se salga de la página
                    if pdf.get_y() + subtask_row_height > pdf.page_break_trigger:
                        pdf.add_page()
                        # Redibujar cabeceras en la nueva página
                        pdf.set_fill_color(*COLOR_HEADER_BG)
                        pdf.set_text_color(*COLOR_HEADER_TEXT)
                        pdf.set_font("Arial", "B", 9) 
                        pdf.set_x(left_margin_subtasks)
                        for header, width in col_widths_subtasks.items():
                            pdf.cell(width, 10, header, 1, 0, "C", fill=True) 
                        pdf.ln()
                        pdf.set_text_color(*COLOR_PRIMARY_TEXT)
                        pdf.set_font("Arial", "", 8) 
                    
                    # Establecer color de fondo alterno para las filas de subtareas
                    if j % 2 == 0:
                        pdf.set_fill_color(255, 255, 255) # Blanco
                    else:
                        pdf.set_fill_color(*COLOR_LIGHT_GRAY) # Gris claro

                    current_x = left_margin_subtasks
                    current_y = pdf.get_y()

                    pdf.set_xy(current_x, current_y)
                    pdf.cell(col_widths_subtasks["Fecha"], subtask_row_height, service.date.strftime('%d/%m/%Y'), 1, 0, "C", fill=True)
                    current_x += col_widths_subtasks["Fecha"]

                    pdf.set_xy(current_x, current_y)
                    pdf.cell(col_widths_subtasks["Lugar"], subtask_row_height, service.place, 1, 0, "L", fill=True)
                    current_x += col_widths_subtasks["Lugar"]

                    pdf.set_xy(current_x, current_y)
                    pdf.multi_cell(col_widths_subtasks["Descripción de Tarea"], LINE_HEIGHT_SUBTASK_TABLE, desc_display, 1, "L", fill=True, ln=0)
                    current_x += col_widths_subtasks["Descripción de Tarea"]

                    pdf.set_xy(current_x, current_y)
                    pdf.cell(col_widths_subtasks["Horas"], subtask_row_height, hours_display, 1, 0, "C", fill=True)
                    
                    pdf.set_y(current_y + subtask_row_height) 
                pdf.ln(5) # Espacio después de cada grupo de sub-tareas

    return pdf.output(dest='S').encode('latin-1')


# --- Rutas de la Aplicación (sin cambios, ya que la lógica está en generate_pdf_report) ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'logged_in' in session and session['logged_in']:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session['logged_in'] = True
            session['username'] = username
            session['user_id'] = user.id 
            session['current_month_selected'] = datetime.now().strftime("%Y-%m")
            session.permanent = True 
            flash('Has iniciado sesión correctamente.', 'success')
            return redirect(url_for('index'))
        else:
            flash('Usuario o contraseña incorrectos.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('username', None)
    session.pop('user_id', None)
    session.pop('current_month_selected', None)
    flash('Has cerrado sesión.', 'info')
    return redirect(url_for('login'))

@app.route('/', methods=['GET'])
@login_required
def index():
    user_id = session.get('user_id')
    selected_month_str = session.get('current_month_selected')
    
    services = [] 
    try:
        selected_month_dt = datetime.strptime(selected_month_str, "%Y-%m")
        start_date = selected_month_dt.replace(day=1).date() 
        
        if selected_month_dt.month == 12:
            end_date = selected_month_dt.replace(year=selected_month_dt.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_date = selected_month_dt.replace(month=selected_month_dt.month + 1, day=1) - timedelta(days=1)
        end_date = end_date.date() 

        # Cargar servicios con sus sub-tareas relacionadas
        services = Service.query.filter(
            Service.user_id == user_id,
            Service.date >= start_date,
            Service.date <= end_date
        ).order_by(Service.date).all()
        
    except ValueError:
        flash("Formato de mes seleccionado inválido.", "danger")
        services = [] 

    for service in services:
        service.date_display = service.date.strftime("%d/%m/%Y")
        _ = service.subtasks 

    return render_template('index.html', services=services, selected_month_str=selected_month_str)

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
        observations = request.form.get('observations', '').strip() 

        no_discount_break = 'no_discount_break' in request.form
        
        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
            entry_time_obj = datetime.strptime(entry_time_str, '%H:%M').time()
            exit_time_obj = datetime.strptime(exit_time_str, '%H:%M').time()
            
            break_duration_val = int(break_duration_str)

            actual_break_for_calculation = 0 if no_discount_break else break_duration_val
            worked_hours = calculate_worked_hours(entry_time_str, exit_time_str, actual_break_for_calculation)
            
            new_service = Service(
                user_id=user_id,
                place=place,
                date=date_obj,
                entry_time=entry_time_obj,
                break_duration=break_duration_val, 
                exit_time=exit_time_obj,
                worked_hours=worked_hours,
                observations=observations
            )
            db.session.add(new_service)
            db.session.flush() 

            subtask_descriptions = request.form.getlist('subtask_description[]')
            subtask_hours = request.form.getlist('subtask_hours[]')

            for desc, hours_str in zip(subtask_descriptions, subtask_hours):
                if desc.strip() and hours_str.strip(): 
                    try:
                        hours_float = float(hours_str)
                        new_subtask = SubTask(
                            service_id=new_service.id,
                            description=desc.strip(),
                            hours=hours_float
                        )
                        db.session.add(new_subtask)
                    except ValueError:
                        flash(f"Error: Las horas para la tarea '{desc}' no son un número válido y no se guardó.", "warning")
                        continue

            db.session.commit() 
            flash('Servicio y tareas añadidas correctamente.', 'success')
            return redirect(url_for('index'))
        except ValueError as e:
            flash(f'Error en el formato de los datos: {e}', 'danger')
        except Exception as e:
            db.session.rollback() 
            flash(f'Error al añadir servicio: {e}', 'danger')

    return render_template('add_service.html') 

@app.route('/edit_service/<int:service_id>', methods=['GET', 'POST'])
@login_required
def edit_service(service_id):
    service = Service.query.filter_by(id=service_id, user_id=session.get('user_id')).first_or_404()

    if request.method == 'POST':
        try:
            service.place = request.form['place']
            service.date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
            service.entry_time = datetime.strptime(request.form['entry_time'], '%H:%M').time()
            service.exit_time = datetime.strptime(request.form['exit_time'], '%H:%M').time()
            service.observations = request.form.get('observations', '').strip()

            no_discount_break = 'no_discount_break' in request.form
            break_duration_val = int(request.form['break_duration'])
            service.break_duration = break_duration_val 

            actual_break_for_calculation = 0 if no_discount_break else break_duration_val
            service.worked_hours = calculate_worked_hours(
                service.entry_time.strftime('%H:%M'),
                service.exit_time.strftime('%H:%M'),
                actual_break_for_calculation 
            )

            SubTask.query.filter_by(service_id=service.id).delete()
            db.session.flush() 

            subtask_descriptions = request.form.getlist('subtask_description[]')
            subtask_hours = request.form.getlist('subtask_hours[]')

            for desc, hours_str in zip(subtask_descriptions, subtask_hours):
                if desc.strip() and hours_str.strip():
                    try:
                        hours_float = float(hours_str)
                        new_subtask = SubTask(
                            service_id=service.id,
                            description=desc.strip(),
                            hours=hours_float
                        )
                        db.session.add(new_subtask)
                    except ValueError:
                        flash(f"Error: Las horas para la tarea '{desc}' no son un número válido y no se guardó.", "warning")
                        continue

            db.session.commit() 
            flash('Servicio y tareas actualizadas correctamente.', 'success')
            return redirect(url_for('index'))
        except ValueError as e:
            flash(f'Error en el formato de los datos: {e}', 'danger')
        except Exception as e:
            db.session.rollback() 
            flash(f'Error al actualizar servicio: {e}', 'danger')
            
    service.date_str = service.date.strftime('%Y-%m-%d')
    service.entry_time_str = service.entry_time.strftime('%H:%M')
    service.exit_time_str = service.exit_time.strftime('%H:%M')
    
    return render_template('edit_service.html', service=service)

@app.route('/delete_service/<int:service_id>', methods=['POST'])
@login_required
def delete_service(service_id):
    service = Service.query.filter_by(id=service_id, user_id=session.get('user_id')).first_or_404()
    try:
        db.session.delete(service) 
        db.session.commit() 
        flash('Servicio eliminado correctamente.', 'success')
    except Exception as e:
        db.session.rollback() 
        flash(f'Error al eliminar servicio: {e}', 'danger')
    return redirect(url_for('index'))

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

@app.route('/download_pdf')
@login_required
def download_pdf():
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

        return send_file(
            io.BytesIO(pdf_output),
            mimetype='application/pdf',
            as_attachment=True, 
            download_name=f'informe_horas_{session["username"]}_{selected_month_str}.pdf'
        )
    except Exception as e:
        flash(f"Error al generar el PDF: {e}", "danger")
        return redirect(url_for('index'))

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

        return send_file(
            io.BytesIO(pdf_output),
            mimetype='application/pdf',
            as_attachment=False 
        )
    except Exception as e:
        flash(f"Error al generar la vista previa del PDF: {e}", "danger")
        return redirect(url_for('index'))


# --- Ejecutar la aplicación ---
if __name__ == '__main__':
    app.run(debug=True)