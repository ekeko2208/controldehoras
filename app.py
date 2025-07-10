from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime, timedelta
import io
import math
from functools import wraps

# Importaciones de ReportLab
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors # Importar colores de ReportLab

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

# Función para generar el informe PDF (REFACTORIZADA para ReportLab)
def generate_pdf_report(user_id, services_data, selected_month_str):
    buffer = io.BytesIO()
    # Usamos landscape(A4) para orientación horizontal
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                            rightMargin=30, leftMargin=30,
                            topMargin=30, bottomMargin=30)
    
    story = [] # Lista de "flowables" que compondrán el documento

    # Estilos de párrafo
    styles = getSampleStyleSheet()
    h1_style = styles['h1']
    h1_style.alignment = TA_CENTER
    h1_style.spaceAfter = 14

    h2_style = styles['h2']
    h2_style.alignment = TA_CENTER
    h2_style.spaceAfter = 10

    normal_style = styles['Normal']
    normal_style.alignment = TA_LEFT
    normal_style.fontSize = 8
    normal_style.leading = 12 # Espacio entre líneas, aumentado a 12

    # Estilo para celdas de tabla
    table_header_style = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        alignment=TA_CENTER,
        textColor=colors.white,
        leading=12
    )

    table_cell_style = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        alignment=TA_LEFT,
        textColor=colors.black,
        leading=12 # Aumentado a 12
    )
    
    table_cell_center_style = ParagraphStyle(
        'TableCellCenter',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        alignment=TA_CENTER,
        textColor=colors.black,
        leading=12 # Aumentado a 12
    )

    # Colores personalizados (ReportLab usa objetos colors)
    COLOR_HEADER_BG = colors.Color(52/255, 73/255, 94/255)
    COLOR_ACCENT = colors.Color(52/255, 152/255, 219/255)
    COLOR_LIGHT_GRAY = colors.Color(240/255, 240/255, 240/255)

    month_num = int(selected_month_str.split('-')[1])
    year_num = selected_month_str.split('-')[0]
    spanish_month_names_local = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]
    month_name_spanish = spanish_month_names_local[month_num - 1].capitalize()

    # --- Página 1: Resumen de Servicios ---
    story.append(Paragraph(f"Reporte de Horas Trabajadas", h1_style))
    story.append(Paragraph(f"Mes: {month_name_spanish} {year_num}", h2_style))
    story.append(Spacer(1, 0.2 * 25.4)) # Espacio de 0.2 pulgadas (aprox 5mm)

    # Datos para la tabla principal
    main_table_data = []
    main_table_data.append([
        Paragraph("Lugar", table_header_style),
        Paragraph("Fecha", table_header_style),
        Paragraph("Entrada", table_header_style),
        Paragraph("Break", table_header_style),
        Paragraph("Salida", table_header_style),
        Paragraph("Horas", table_header_style),
        Paragraph("Observaciones", table_header_style)
    ])

    total_hours_month = 0.0 

    for i, service in enumerate(services_data):
        date_display = service.date.strftime("%d/%m/%Y")
        entry_time_display = service.entry_time.strftime("%H:%M")
        exit_time_display = service.exit_time.strftime("%H:%M")
        obs_display = service.observations if service.observations else ""
        
        # Usar Paragraph para el contenido de las celdas, permite wrapping
        main_table_data.append([
            Paragraph(service.place, table_cell_style),
            Paragraph(date_display, table_cell_center_style),
            Paragraph(entry_time_display, table_cell_center_style),
            Paragraph(str(service.break_duration), table_cell_center_style),
            Paragraph(exit_time_display, table_cell_center_style),
            Paragraph(f"{service.worked_hours:.2f}", table_cell_center_style),
            Paragraph(obs_display, table_cell_style)
        ])
        total_hours_month += service.worked_hours

    # Anchos de columna para la tabla principal (en unidades de ReportLab)
    # A4 landscape es 297mm x 210mm. Margenes de 30mm a cada lado.
    # Ancho útil = 297 - 2*30 = 237mm
    # Ajustados para sumar 237mm
    col_widths_main_rl = [35, 20, 18, 18, 18, 18, 110] 

    main_table = Table(main_table_data, colWidths=[col * 1 for col in col_widths_main_rl]) 
    main_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), COLOR_HEADER_BG), # Cabecera
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 9),
        ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ('TOPPADDING', (0,0), (-1,0), 6),

        ('GRID', (0,0), (-1,-1), 0.5, colors.grey), # Bordes de la tabla
        ('BOX', (0,0), (-1,-1), 1, colors.black),
    ]))
    
    # Aplicar colores alternos dinámicamente
    for row_idx in range(1, len(main_table_data)): # Empezar desde la primera fila de datos (índice 1)
        if row_idx % 2 == 0: # Si la fila de datos es par (0-indexed en TableStyle para datos)
            main_table.setStyle(TableStyle([('BACKGROUND', (0, row_idx), (-1, row_idx), COLOR_LIGHT_GRAY)]))
        else:
            main_table.setStyle(TableStyle([('BACKGROUND', (0, row_idx), (-1, row_idx), colors.white)]))


    story.append(main_table)
    story.append(Spacer(1, 0.2 * 25.4))

    total_hours_paragraph = Paragraph(f"<b>Total de horas trabajadas en el mes: {total_hours_month:.2f} horas</b>", 
                                      ParagraphStyle('TotalHours', parent=styles['Normal'], alignment=TA_RIGHT, fontSize=12, textColor=COLOR_ACCENT))
    story.append(total_hours_paragraph)

    # --- Página 2: Tareas Detalladas ---
    has_subtasks = any(service.subtasks for service in services_data)
    if has_subtasks:
        story.append(PageBreak()) # Salto de página para la sección de detalles
        story.append(Paragraph("Detalle de Tareas por Servicio", h1_style))
        story.append(Paragraph(f"Mes: {month_name_spanish} {year_num}", h2_style))
        story.append(Spacer(1, 0.2 * 25.4))

        for service in services_data:
            if service.subtasks:
                # Encabezado para cada servicio con sub-tareas
                service_header_style = ParagraphStyle(
                    'ServiceHeader',
                    parent=styles['Normal'],
                    fontName='Helvetica-Bold',
                    fontSize=10,
                    textColor=COLOR_ACCENT,
                    alignment=TA_LEFT,
                    spaceBefore=10,
                    spaceAfter=5
                )
                story.append(Paragraph(f"<b>Servicio:</b> {service.place} - {service.date.strftime('%d/%m/%Y')}", service_header_style))

                subtask_data = []
                subtask_data.append([
                    Paragraph("Fecha", table_header_style),
                    Paragraph("Lugar", table_header_style),
                    Paragraph("Descripción de Tarea", table_header_style),
                    Paragraph("Horas", table_header_style)
                ])

                for j, subtask in enumerate(service.subtasks):
                    subtask_data.append([
                        Paragraph(service.date.strftime('%d/%m/%Y'), table_cell_center_style),
                        Paragraph(service.place, table_cell_style),
                        Paragraph(subtask.description, table_cell_style),
                        Paragraph(f"{subtask.hours:.1f}", table_cell_center_style)
                    ])
                
                # Anchos de columna para la tabla de sub-tareas (ajustados para sumar 237mm)
                col_widths_subtasks_rl = [25, 45, 147, 20] 
                subtask_table = Table(subtask_data, colWidths=[col * 1 for col in col_widths_subtasks_rl]) 
                subtask_table.setStyle(TableStyle([
                    ('BACKGROUND', (0,0), (-1,0), COLOR_HEADER_BG),
                    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                    ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0,0), (-1,0), 9),
                    ('BOTTOMPADDING', (0,0), (-1,0), 6),
                    ('TOPPADDING', (0,0), (-1,0), 6),

                    ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
                    ('BOX', (0,0), (-1,-1), 1, colors.black),
                ]))

                # Aplicar colores alternos dinámicamente para las subtareas
                for row_idx in range(1, len(subtask_data)):
                    if row_idx % 2 == 0:
                        subtask_table.setStyle(TableStyle([('BACKGROUND', (0, row_idx), (-1, row_idx), COLOR_LIGHT_GRAY)]))
                    else:
                        subtask_table.setStyle(TableStyle([('BACKGROUND', (0, row_idx), (-1, row_idx), colors.white)]))

                story.append(subtask_table)
                story.append(Spacer(1, 0.2 * 25.4)) # Espacio entre servicios

    # Construir el documento
    doc.build(story)
    
    # Retornar el contenido del buffer
    buffer.seek(0)
    return buffer.getvalue()


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
    with app.app_context():
        db.create_all() 

        if not User.query.filter_by(username='admin').first():
            admin_user = User(username='admin')
            admin_user.set_password('password123') 
            db.session.add(admin_user)
            db.session.commit()
            print("Usuario 'admin' creado por defecto.")
            
    app.run(debug=True)