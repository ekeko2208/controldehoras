from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime, timedelta
import io
import math
from functools import wraps
from collections import defaultdict # Para agrupar tareas
import csv # Importar para exportación CSV

# Importaciones de ReportLab
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape, portrait
from reportlab.lib.units import mm # Para trabajar con milímetros

# --- Configuración de la aplicación Flask ---
app = Flask(__name__)

# Clave secreta (¡IMPORTANTE! Cambiar por una clave más segura en producción)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'una_clave_secreta_muy_larga_y_aleatoria_para_sesiones_seguras_2024')

# Configuración de la base de datos
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
    break_duration = db.Column(db.Integer, nullable=False) 
    exit_time = db.Column(db.Time, nullable=False) 
    worked_hours = db.Column(db.Float, nullable=False) 
    observations = db.Column(db.Text, nullable=True) 

    def __repr__(self):
        return f'<Service {self.id} - {self.date} - {self.place}>'

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
def calculate_worked_hours(entry_time_str, exit_time_str, break_duration_min):
    try:
        entry = datetime.strptime(entry_time_str, '%H:%M')
        exit = datetime.strptime(exit_time_str, '%H:%M')
        
        total_minutes = (exit - entry).total_seconds() / 60
        
        worked_minutes = total_minutes - break_duration_min
        
        worked_hours = round(worked_minutes / 60, 2)
        return worked_hours
    except ValueError:
        return 0.0 

# Función para generar el informe PDF de Servicios (usando ReportLab)
def generate_pdf_report(user_id, services_data, selected_month_str):
    buffer = io.BytesIO()
    # Configurar el PDF en milímetros, tamaño A4 y orientación HORIZONTAL ('L')
    c = canvas.Canvas(buffer, pagesize=landscape(A4)) 
    
    # Obtener el nombre del mes en español
    month_num = int(selected_month_str.split('-')[1])
    year_num = selected_month_str.split('-')[0]
    spanish_month_names_local = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]
    month_name_spanish = spanish_month_names_local[month_num - 1].capitalize()

    # Título del informe
    c.setFont("Helvetica-Bold", 16) 
    page_width = landscape(A4)[0] # Ancho de la página en puntos (horizontal)
    c.drawCentredString(page_width / 2, landscape(A4)[1] - 20*mm, f"Reporte de horas trabajadas para el mes de {month_name_spanish} {year_num}") 
    
    # Definir anchos de columna para la tabla del PDF (en mm, luego convertidos a puntos)
    col_widths_mm = {
        "Lugar": 40,        
        "Fecha": 25,        
        "Entrada": 20,      
        "Break": 20,        
        "Salida": 20,       
        "Horas": 20,        
        "Observaciones": 100 
    }
    
    # Convertir anchos de columna a puntos
    col_widths_pts = {k: v * mm for k, v in col_widths_mm.items()}
    
    # Calcular margen izquierdo para centrar la tabla
    total_table_width_pts = sum(col_widths_pts.values())
    left_margin_pts = (page_width - total_table_width_pts) / 2
    
    # Posición Y inicial para las cabeceras de la tabla
    current_y = landscape(A4)[1] - 50*mm 

    # Dibujar cabeceras de la tabla
    c.setFont("Helvetica-Bold", 8) 
    x_pos = left_margin_pts
    header_height = 7*mm
    for header, width_pts in col_widths_pts.items():
        c.rect(x_pos, current_y - header_height, width_pts, header_height) # Dibujar rectángulo de la celda
        c.drawCentredString(x_pos + width_pts / 2, current_y - header_height + 2*mm, header) # Dibujar texto centrado
        x_pos += width_pts
    current_y -= header_height # Mover hacia abajo después de las cabeceras

    # Dibujar datos de la tabla
    c.setFont("Helvetica", 7) 
    total_hours_month = 0.0 
    row_height = 6*mm # Altura predeterminada de la fila

    for service in services_data:
        # Verificar si se necesita una nueva página antes de dibujar la fila actual
        if current_y < 30*mm: # Si queda menos de 30mm desde el fondo, añadir nueva página
            c.showPage() # Nueva página
            c.setFont("Helvetica-Bold", 8)
            current_y = landscape(A4)[1] - 20*mm # Reiniciar Y para la nueva página
            x_pos = left_margin_pts
            for header, width_pts in col_widths_pts.items():
                c.rect(x_pos, current_y - header_height, width_pts, header_height)
                c.drawCentredString(x_pos + width_pts / 2, current_y - header_height + 2*mm, header)
                x_pos += width_pts
            current_y -= header_height
            c.setFont("Helvetica", 7)

        date_display = service.date.strftime("%d/%m/%Y")
        entry_time_display = service.entry_time.strftime("%H:%M")
        exit_time_display = service.exit_time.strftime("%H:%M")

        obs_display = service.observations if service.observations else ""
        # Truncar observaciones si son demasiado largas
        max_obs_width_pts = col_widths_pts["Observaciones"] * 0.9 # Dejar algo de padding
        if c.stringWidth(obs_display, "Helvetica", 7) > max_obs_width_pts:
            while c.stringWidth(obs_display + "...", "Helvetica", 7) > max_obs_width_pts and len(obs_display) > 0:
                obs_display = obs_display[:-1]
            obs_display += "..."

        x_pos = left_margin_pts
        
        # Dibujar celdas para la fila actual
        cells_data = [
            (col_widths_pts["Lugar"], service.place, "L"),
            (col_widths_pts["Fecha"], date_display, "C"),
            (col_widths_pts["Entrada"], entry_time_display, "C"),
            (col_widths_pts["Break"], str(service.break_duration), "C"),
            (col_widths_pts["Salida"], exit_time_display, "C"),
            (col_widths_pts["Horas"], f"{service.worked_hours:.2f}", "C"),
            (col_widths_pts["Observaciones"], obs_display, "L")
        ]

        for width_pts, text, align in cells_data:
            c.rect(x_pos, current_y - row_height, width_pts, row_height)
            if align == "L":
                c.drawString(x_pos + 2*mm, current_y - row_height + 2*mm, text)
            elif align == "C":
                c.drawCentredString(x_pos + width_pts / 2, current_y - row_height + 2*mm, text)
            elif align == "R":
                c.drawRightString(x_pos + width_pts - 2*mm, current_y - row_height + 2*mm, text)
            x_pos += width_pts
        
        current_y -= row_height # Mover hacia abajo para la siguiente fila
        total_hours_month += service.worked_hours

    # Dibujar el total de horas
    c.setFont("Helvetica-Bold", 10) 
    c.drawRightString(page_width - left_margin_pts - 10*mm, current_y - 10*mm, f"Total de horas trabajadas en el mes: {total_hours_month:.2f} horas")

    c.save() # Guardar el PDF
    return buffer.getvalue() # Retornar el PDF como bytes

# Función para generar el informe PDF de Tareas (usando ReportLab)
def generate_tasks_pdf_report(tasks_summary_data, selected_month_str):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=portrait(A4)) # Orientación vertical para resumen de tareas

    month_num = int(selected_month_str.split('-')[1])
    year_num = selected_month_str.split('-')[0]
    spanish_month_names_local = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]
    month_name_spanish = spanish_month_names_local[month_num - 1].capitalize()

    c.setFont("Helvetica-Bold", 16)
    page_width = portrait(A4)[0]
    c.drawCentredString(page_width / 2, portrait(A4)[1] - 20*mm, f"Resumen de Horas por Tarea/Lugar para {month_name_spanish} {year_num}")
    
    current_y = portrait(A4)[1] - 50*mm

    c.setFont("Helvetica-Bold", 10)
    col_widths_mm = {"Tarea/Lugar": 120, "Total Horas": 40}
    col_widths_pts = {k: v * mm for k, v in col_widths_mm.items()}
    
    total_table_width_pts = sum(col_widths_pts.values())
    left_margin_pts = (page_width - total_table_width_pts) / 2
    
    x_pos = left_margin_pts
    header_height = 7*mm
    for header, width_pts in col_widths_pts.items():
        c.rect(x_pos, current_y - header_height, width_pts, header_height)
        c.drawCentredString(x_pos + width_pts / 2, current_y - header_height + 2*mm, header)
        x_pos += width_pts
    current_y -= header_height

    c.setFont("Helvetica", 9)
    row_height = 6*mm
    for task, total_hours in tasks_summary_data.items():
        # Verificar si se necesita una nueva página
        if current_y < 30*mm:
            c.showPage()
            c.setFont("Helvetica-Bold", 10)
            current_y = portrait(A4)[1] - 20*mm
            x_pos = left_margin_pts
            for header, width_pts in col_widths_pts.items():
                c.rect(x_pos, current_y - header_height, width_pts, header_height)
                c.drawCentredString(x_pos + width_pts / 2, current_y - header_height + 2*mm, header)
                x_pos += width_pts
            current_y -= header_height
            c.setFont("Helvetica", 9)

        x_pos = left_margin_pts

        c.rect(x_pos, current_y - row_height, col_widths_pts["Tarea/Lugar"], row_height)
        c.drawString(x_pos + 2*mm, current_y - row_height + 2*mm, task)
        x_pos += col_widths_pts["Tarea/Lugar"]

        c.rect(x_pos, current_y - row_height, col_widths_pts["Total Horas"], row_height)
        c.drawCentredString(x_pos + col_widths_pts["Total Horas"] / 2, current_y - row_height + 2*mm, f"{total_hours:.2f}")
        
        current_y -= row_height

    c.setFont("Helvetica-Bold", 10)
    total_general_hours = sum(tasks_summary_data.values())
    c.drawRightString(page_width - left_margin_pts - 10*mm, current_y - 10*mm, f"Total General de Horas: {total_general_hours:.2f} horas")

    c.save()
    return buffer.getvalue()


# --- Rutas de la Aplicación ---

# Ruta para el inicio de sesión
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

# Ruta para cerrar sesión
@app.route('/logout')
def logout():
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
    
    services = [] 
    total_hours_display = "00:00" 
    search_query = request.args.get('search', '').strip() 

    try:
        selected_month_dt = datetime.strptime(selected_month_str, "%Y-%m")
        start_date = selected_month_dt.replace(day=1).date() 
        
        if selected_month_dt.month == 12:
            end_date = selected_month_dt.replace(year=selected_month_dt.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_date = selected_month_dt.replace(month=selected_month_dt.month + 1, day=1) - timedelta(days=1)
        end_date = end_date.date() 

        services_query = Service.query.filter(
            Service.user_id == user_id,
            Service.date >= start_date,
            Service.date <= end_date
        )

        if search_query:
            services_query = services_query.filter(
                (Service.observations.ilike(f'%{search_query}%')) |
                (Service.place.ilike(f'%{search_query}%'))
            )

        services = services_query.order_by(Service.date.desc(), Service.entry_time.desc()).all()
        
        total_minutes_overall = sum(s.worked_hours * 60 for s in services)
        total_hours_overall = int(total_minutes_overall // 60)
        total_remaining_minutes = int(total_minutes_overall % 60)
        total_hours_display = f"{total_hours_overall:02d}:{total_remaining_minutes:02d}"


    except ValueError:
        flash("Formato de mes seleccionado inválido.", "danger")
        services = [] 
        total_hours_display = "00:00"
    except Exception as e:
        flash(f"Ocurrió un error al cargar los servicios: {e}", "danger")
        services = []
        total_hours_display = "00:00"

    for service in services:
        service.date_display = service.date.strftime("%d/%m/%Y")

    return render_template('index.html', 
                           services=services, 
                           total_hours_display=total_hours_display,
                           search_query=search_query) 

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
        observations = request.form.get('observations', '').strip() 

        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
            entry_time_obj = datetime.strptime(entry_time_str, '%H:%M').time()
            exit_time_obj = datetime.strptime(exit_time_str, '%H:%M').time()
            
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
            db.session.commit() 
            flash('Servicio añadido correctamente.', 'success')
            return redirect(url_for('index'))
        except ValueError as e:
            flash(f'Error en el formato de los datos: {e}', 'danger')
        except Exception as e:
            db.session.rollback() 
            flash(f'Error al añadir servicio: {e}', 'danger')

    return render_template('add_service.html') 

# Ruta para editar un servicio existente
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
            service.break_duration = 0 if no_discount_break else int(request.form['break_duration'])

            service.worked_hours = calculate_worked_hours(
                service.entry_time.strftime('%H:%M'),
                service.exit_time.strftime('%H:%M'),
                service.break_duration
            )

            db.session.commit() 
            flash('Servicio actualizado correctamente.', 'success')
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

# Ruta para eliminar un servicio
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

        return send_file(
            io.BytesIO(pdf_output),
            mimetype='application/pdf',
            as_attachment=False 
        )
    except Exception as e:
        flash(f"Error al generar la vista previa del PDF: {e}", "danger")
        return redirect(url_for('index'))

# Ruta para exportar servicios a CSV. (AÑADIDA)
@app.route('/export_csv')
@login_required
def export_csv():
    user_id = session['user_id']
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

        # Crear un buffer en memoria para el archivo CSV.
        si = io.StringIO()
        cw = csv.writer(si)

        # Escribir la cabecera del CSV.
        cw.writerow(['Fecha', 'Lugar', 'Hora Entrada', 'Duracion Break (min)', 'Hora Salida', 'Horas Trabajadas', 'Observaciones'])

        # Escribir los datos de cada servicio.
        for service in services:
            cw.writerow([
                service.date.strftime('%Y-%m-%d'),
                service.place,
                service.entry_time.strftime('%H:%M'),
                service.break_duration,
                service.exit_time.strftime('%H:%M'),
                f"{service.worked_hours:.2f}",
                service.observations if service.observations else ''
            ])

        output = io.BytesIO(si.getvalue().encode('utf-8'))
        output.seek(0)

        # Enviar el archivo CSV como descarga.
        return send_file(output,
                         mimetype='text/csv',
                         download_name=f'servicios_{session["username"]}_{selected_month_str}.csv',
                         as_attachment=True)
    except Exception as e:
        flash(f"Error al exportar CSV: {e}", "danger")
        return redirect(url_for('index'))


# NUEVA RUTA: Resumen de Tareas Específicas
@app.route('/tasks_summary', methods=['GET', 'POST'])
@login_required
def tasks_summary():
    user_id = session.get('user_id')
    selected_month_str = session.get('current_month_selected')

    if request.method == 'POST':
        form_selected_month = request.form.get('selected_month')
        if form_selected_month:
            session['current_month_selected'] = form_selected_month
            selected_month_str = form_selected_month 
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

        services = Service.query.filter(
            Service.user_id == user_id,
            Service.date >= start_date,
            Service.date <= end_date
        ).all()

        grouped_hours = defaultdict(float)
        for service in services:
            grouped_hours[service.place] += service.worked_hours
        
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
        if 'username' in request.form:
            new_username = request.form['username'].strip()
            if new_username and new_username != user.username:
                existing_user = User.query.filter_by(username=new_username).first()
                if existing_user and existing_user.id != user.id:
                    flash('El nombre de usuario ya está en uso. Por favor, elige otro.', 'danger')
                else:
                    user.username = new_username
                    session['username'] = new_username 
                    db.session.commit()
                    flash('Nombre de usuario actualizado correctamente.', 'success')
            elif new_username == user.username:
                flash('El nombre de usuario es el mismo que el actual.', 'info')
            else:
                flash('El nombre de usuario no puede estar vacío.', 'danger')

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
        
        return redirect(url_for('profile')) 

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
    if request.method == 'POST':
        new_password = request.form['new_password']
        confirm_new_password = request.form['confirm_new_password']
        
        if not new_password or len(new_password) < 6:
            flash('La nueva contraseña debe tener al menos 6 caracteres.', 'danger')
        elif new_password != confirm_new_password:
            flash('Las contraseñas no coinciden.', 'danger')
        else:
            flash('Tu contraseña ha sido restablecida correctamente (funcionalidad no implementada en esta demo).', 'success')
            return redirect(url_for('login'))
    
    return render_template('reset_password.html', token=token)


# Ruta para el registro de nuevos usuarios
@app.route('/register', methods=['GET', 'POST'])
def register():
    REGISTRATION_CODE = "arles2208." 

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        registration_code = request.form['registration_code'] 

        if registration_code != REGISTRATION_CODE:
            flash('Código de registro incorrecto.', 'danger')
            return render_template('register.html')

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

        new_user = User(username=username)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        flash('¡Registro exitoso! Ahora puedes iniciar sesión.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')


# --- Ejecutar la aplicación ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all() 

        if not User.query.filter_by(username='Arles').first():
            admin_user = User(username='Arles')
            admin_user.set_password('arles2208') 
            db.session.add(admin_user)
            db.session.commit()
            print("Usuario 'Arles' creado por defecto.")
            
    app.run(debug=True)