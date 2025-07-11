import os
import json
from datetime import datetime, timedelta
from functools import wraps
from collections import defaultdict
from io import BytesIO

from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, login_user, LoginManager, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import csv # Importar para exportación CSV

# Importaciones de ReportLab
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape, portrait # Import landscape and portrait
from reportlab.lib.units import mm # Para trabajar con milímetros
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch # Para ReportLab TableStyle


# --- Configuración de la aplicación Flask ---
app = Flask(__name__)

# Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_super_secret_key_that_is_very_long_and_random_2024') # Use environment variable for production
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///site.db') # Use environment variable for production
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize SQLAlchemy
db = SQLAlchemy(app)

# Setup Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' # Redirect to login page if not authenticated

# User Loader for Flask-Login
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Database Models
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False) # Increased to 80 for longer usernames
    password_hash = db.Column(db.String(512), nullable=False) # Increased to 512 for scrypt hashes
    services = db.relationship('Service', backref='author', lazy=True) # One-to-many relationship with Service

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"User('{self.username}')"

class Service(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    place = db.Column(db.String(100), nullable=False)
    entry_time = db.Column(db.Time, nullable=False)
    break_duration = db.Column(db.Integer, default=0) # Break duration in minutes
    exit_time = db.Column(db.Time, nullable=False)
    worked_hours = db.Column(db.Float, nullable=False)
    observations = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    # New field to store specific tasks as a JSON string
    specific_tasks = db.Column(db.Text, nullable=True) # Stores JSON: [{"description": "task name", "duration": 1.5}]

    def __repr__(self):
        return f"Service('{self.date}', '{self.place}', '{self.worked_hours}')"

# Helper function to calculate worked hours
def calculate_worked_hours(entry_time_str, exit_time_str, break_duration_minutes):
    try:
        entry_dt = datetime.strptime(entry_time_str, '%H:%M')
        exit_dt = datetime.strptime(exit_time_str, '%H:%M')

        if exit_dt < entry_dt:
            # Handle cases where exit time is on the next day
            exit_dt += timedelta(days=1)

        total_duration = exit_dt - entry_dt
        break_td = timedelta(minutes=break_duration_minutes)
        worked_duration = total_duration - break_td

        worked_hours = worked_duration.total_seconds() / 3600
        return max(0.0, worked_hours) # Ensure hours are not negative
    except ValueError:
        return None # Return None if time format is incorrect

# Decorator to redirect authenticated users from login/register
def redirect_authenticated(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.is_authenticated:
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

# --- Routes ---

@app.route("/")
@app.route("/index")
@login_required
def index():
    current_month_str = session.get('current_month', datetime.now().strftime('%Y-%m'))
    year, month = map(int, current_month_str.split('-'))

    # Filter services by month and year for the current user
    services_query = Service.query.filter_by(user_id=current_user.id).filter(
        db.extract('year', Service.date) == year,
        db.extract('month', Service.date) == month
    )

    search_query = request.args.get('search')
    if search_query:
        services_query = services_query.filter(
            (Service.place.ilike(f'%{search_query}%')) |
            (Service.observations.ilike(f'%{search_query}%'))
        )

    services = services_query.order_by(Service.date.asc(), Service.entry_time.asc()).all()

    total_hours = sum(service.worked_hours for service in services)
    total_hours_display = f"{total_hours:.2f} horas"

    spanish_month_names = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]

    return render_template('index.html',
                           services=services,
                           total_hours_display=total_hours_display,
                           current_month=current_month_str,
                           spanish_month_names=spanish_month_names,
                           current_username=current_user.username)

@app.route("/load_month", methods=['POST'])
@login_required
def load_month():
    selected_month = request.form.get('selected_month')
    if selected_month:
        session['current_month'] = selected_month
    return redirect(url_for('index'))

@app.route("/add_service", methods=['GET', 'POST'])
@login_required
def add_service():
    if request.method == 'POST':
        date_str = request.form['date']
        place = request.form['place']
        entry_time_str = request.form['entry_time']
        break_duration = int(request.form['break_duration'])
        exit_time_str = request.form['exit_time']
        observations = request.form.get('observations')

        worked_hours = calculate_worked_hours(entry_time_str, exit_time_str, break_duration)

        if worked_hours is None:
            flash('Formato de hora inválido. Por favor, usa HH:MM.', 'danger')
            return redirect(url_for('add_service'))
        if worked_hours < 0:
            flash('La hora de salida no puede ser anterior a la hora de entrada, considerando el descanso.', 'danger')
            return redirect(url_for('add_service'))

        # Handle specific tasks
        specific_task_descriptions = request.form.getlist('specific_task_description[]')
        specific_task_durations = request.form.getlist('specific_task_duration[]')
        
        specific_tasks_list = []
        for desc, dur in zip(specific_task_descriptions, specific_task_durations):
            if desc and dur: # Only add if both description and duration are provided
                try:
                    duration_float = float(dur)
                    if duration_float > 0:
                        specific_tasks_list.append({"description": desc.strip(), "duration": duration_float})
                except ValueError:
                    flash(f'Duración inválida para la tarea "{desc}". Debe ser un número.', 'warning')
                    # Continue to process other tasks, but inform user
        
        # Convert list of dicts to JSON string
        specific_tasks_json = json.dumps(specific_tasks_list) if specific_tasks_list else None

        try:
            new_service = Service(
                date=datetime.strptime(date_str, '%Y-%m-%d').date(),
                place=place,
                entry_time=datetime.strptime(entry_time_str, '%H:%M').time(),
                break_duration=break_duration,
                exit_time=datetime.strptime(exit_time_str, '%H:%M').time(),
                worked_hours=worked_hours,
                observations=observations,
                user_id=current_user.id,
                specific_tasks=specific_tasks_json # Save the JSON string
            )
            db.session.add(new_service)
            db.session.commit()
            flash('Servicio añadido exitosamente!', 'success')
            return redirect(url_for('index'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error al añadir el servicio: {e}', 'danger')
            return redirect(url_for('add_service'))

    # Set default date to today and default month for the month input
    default_date = datetime.now().strftime('%Y-%m-%d')
    current_month_for_input = datetime.now().strftime('%Y-%m')

    spanish_month_names = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]

    return render_template('add_service.html',
                           default_date=default_date,
                           current_month=current_month_for_input,
                           spanish_month_names=spanish_month_names,
                           current_username=current_user.username)


@app.route("/edit_service/<int:service_id>", methods=['GET', 'POST'])
@login_required
def edit_service(service_id):
    service = Service.query.get_or_404(service_id)
    if service.user_id != current_user.id:
        flash('No tienes permiso para editar este servicio.', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        service.date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
        service.place = request.form['place']
        service.entry_time = datetime.strptime(request.form['entry_time'], '%H:%M').time()
        service.break_duration = int(request.form['break_duration'])
        service.exit_time = datetime.strptime(request.form['exit_time'], '%H:%M').time()
        service.observations = request.form.get('observations')

        worked_hours = calculate_worked_hours(
            request.form['entry_time'],
            request.form['exit_time'],
            service.break_duration
        )

        if worked_hours is None:
            flash('Formato de hora inválido. Por favor, usa HH:MM.', 'danger')
            return redirect(url_for('edit_service', service_id=service.id))
        if worked_hours < 0:
            flash('La hora de salida no puede ser anterior a la hora de entrada, considerando el descanso.', 'danger')
            return redirect(url_for('edit_service', service_id=service.id))
        
        service.worked_hours = worked_hours

        # Handle specific tasks for editing
        specific_task_descriptions = request.form.getlist('specific_task_description[]')
        specific_task_durations = request.form.getlist('specific_task_duration[]')
        
        specific_tasks_list = []
        for desc, dur in zip(specific_task_descriptions, specific_task_durations):
            if desc and dur: # Only add if both description and duration are provided
                try:
                    duration_float = float(dur)
                    if duration_float > 0:
                        specific_tasks_list.append({"description": desc.strip(), "duration": duration_float})
                except ValueError:
                    flash(f'Duración inválida para la tarea "{desc}". Debe ser un número.', 'warning')
                    # Continue to process other tasks, but inform user
        
        service.specific_tasks = json.dumps(specific_tasks_list) if specific_tasks_list else None


        try:
            db.session.commit()
            flash('Servicio actualizado exitosamente!', 'success')
            return redirect(url_for('index'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error al actualizar el servicio: {e}', 'danger')
            return redirect(url_for('edit_service', service_id=service.id))

    # For GET request, parse existing specific tasks
    existing_specific_tasks = []
    if service.specific_tasks:
        try:
            existing_specific_tasks = json.loads(service.specific_tasks)
        except json.JSONDecodeError:
            flash('Error al cargar tareas específicas existentes.', 'warning')
            existing_specific_tasks = []

    spanish_month_names = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]

    return render_template('edit_service.html',
                           service=service,
                           existing_specific_tasks=existing_specific_tasks, # Pass to template
                           spanish_month_names=spanish_month_names,
                           current_username=current_user.username)

@app.route("/delete_service/<int:service_id>", methods=['POST'])
@login_required
def delete_service(service_id):
    service = Service.query.get_or_404(service_id)
    if service.user_id != current_user.id:
        flash('No tienes permiso para eliminar este servicio.', 'danger')
        return redirect(url_for('index'))
    try:
        db.session.delete(service)
        db.session.commit()
        flash('Servicio eliminado exitosamente!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al eliminar el servicio: {e}', 'danger')
    return redirect(url_for('index'))

@app.route("/register", methods=['GET', 'POST'])
@redirect_authenticated
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
            return render_template('register.html') # Renderiza de nuevo el formulario para que el usuario pueda corregir

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

@app.route("/login", methods=['GET', 'POST'])
@redirect_authenticated
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            flash('Inicio de sesión exitoso!', 'success')
            # Set default month in session for the logged-in user
            session['current_month'] = datetime.now().strftime('%Y-%m')
            session['current_tasks_month'] = datetime.now().strftime('%Y-%m') # Also for tasks summary
            return redirect(url_for('index'))
        else:
            flash('Nombre de usuario o contraseña incorrectos.', 'danger')
    return render_template('login.html')

@app.route("/logout")
@login_required
def logout():
    logout_user()
    session.pop('current_month', None) # Clear session month on logout
    session.pop('current_tasks_month', None) # Clear tasks month on logout
    flash('Has cerrado sesión exitosamente.', 'info')
    return redirect(url_for('login'))

@app.route("/profile", methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        new_username = request.form['username']
        new_password = request.form.get('new_password') # Usar .get para que no falle si no se envía

        # Lógica para actualizar nombre de usuario
        if new_username != current_user.username:
            existing_user = User.query.filter_by(username=new_username).first()
            if existing_user and existing_user.id != current_user.id:
                flash('Este nombre de usuario ya está en uso.', 'danger')
                return redirect(url_for('profile'))
            current_user.username = new_username
            flash('Nombre de usuario actualizado!', 'success')
        
        # Lógica para cambiar contraseña
        if new_password: # Solo procesar si se ha proporcionado una nueva contraseña
            current_password = request.form.get('current_password')
            confirm_new_password = request.form.get('confirm_new_password')

            if not current_user.check_password(current_password):
                flash('La contraseña actual es incorrecta.', 'danger')
            elif len(new_password) < 6:
                flash('La nueva contraseña debe tener al menos 6 caracteres.', 'danger')
            elif new_password != confirm_new_password:
                flash('La nueva contraseña y la confirmación no coinciden.', 'danger')
            else:
                current_user.set_password(new_password)
                flash('Contraseña actualizada!', 'success')
        
        try:
            db.session.commit()
            # No es necesario flashear "Perfil actualizado exitosamente!" aquí si ya flasheamos los mensajes específicos
        except Exception as e:
            db.session.rollback()
            flash(f'Error al actualizar el perfil: {e}', 'danger')
        
        return redirect(url_for('profile'))
    
    return render_template('profile.html', user=current_user, current_username=current_user.username)


@app.route("/forgot_password", methods=['GET', 'POST'])
@redirect_authenticated
def forgot_password():
    if request.method == 'POST':
        username = request.form['username'].strip()
        user = User.query.filter_by(username=username).first()

        if user:
            # En un entorno real, aquí generarías un token único y lo enviarías por correo electrónico.
            # Por simplicidad, aquí solo flashearemos un mensaje.
            # reset_link = url_for('reset_password', token='dummy_token_for_demo', _external=True)
            flash(f'Si el usuario existe, se ha enviado un enlace de restablecimiento de contraseña (funcionalidad de envío de email no implementada en esta demo).', 'info')
        else:
            # Es buena práctica no revelar si el usuario existe o no por seguridad.
            flash('Si el usuario existe, se ha enviado un enlace de restablecimiento de contraseña (funcionalidad de envío de email no implementada en esta demo).', 'info')
        return redirect(url_for('login'))
    return render_template('forgot_password.html')

@app.route("/reset_password/<token>", methods=['GET', 'POST'])
@redirect_authenticated
def reset_password(token):
    # En un entorno real, aquí validarías el token:
    # user_id = verify_reset_token(token) # Necesitarías implementar esta función
    # if not user_id:
    #     flash('El enlace de restablecimiento es inválido o ha expirado.', 'danger')
    #     return redirect(url_for('forgot_password'))
    
    # Para esta demo, asumimos que el token es válido y lo pasamos al formulario
    # y el usuario puede cambiar la contraseña.
    # En un entorno real, buscarías al usuario por el user_id del token.
    # user = User.query.get(user_id) 

    if request.method == 'POST':
        new_password = request.form['new_password']
        confirm_new_password = request.form['confirm_new_password']
        
        if not new_password or len(new_password) < 6:
            flash('La nueva contraseña debe tener al menos 6 caracteres.', 'danger')
        elif new_password != confirm_new_password:
            flash('Las contraseñas no coinciden.', 'danger')
        else:
            # Aquí iría la lógica para actualizar la contraseña del usuario real
            # user.set_password(new_password)
            # db.session.commit()
            flash('Tu contraseña ha sido restablecida correctamente (funcionalidad de actualización no implementada en esta demo).', 'success')
            return redirect(url_for('login'))
    
    return render_template('reset_password.html', token=token)


@app.route("/export_csv")
@login_required
def export_csv():
    current_month_str = session.get('current_month', datetime.now().strftime('%Y-%m'))
    year, month = map(int, current_month_str.split('-'))

    services = Service.query.filter_by(user_id=current_user.id).filter(
        db.extract('year', Service.date) == year,
        db.extract('month', Service.date) == month
    ).order_by(Service.date.asc(), Service.entry_time.asc()).all()

    si = BytesIO()
    # Write CSV header
    si.write(b'Fecha,Lugar,Entrada,Descanso (min),Salida,Horas Trabajadas,Observaciones,Tareas Especificas\n')

    for service in services:
        # Prepare specific tasks for CSV
        specific_tasks_csv = ""
        if service.specific_tasks:
            try:
                tasks = json.loads(service.specific_tasks)
                task_strings = []
                for task in tasks:
                    task_strings.append(f"{task.get('description', 'N/A')} ({task.get('duration', 0):.2f}h)")
                specific_tasks_csv = "; ".join(task_strings)
            except json.JSONDecodeError:
                specific_tasks_csv = "Error al cargar tareas"

        line = f"{service.date.strftime('%d/%m/%Y')}," \
               f"{service.place}," \
               f"{service.entry_time.strftime('%H:%M')}," \
               f"{service.break_duration}," \
               f"{service.exit_time.strftime('%H:%M')}," \
               f"{service.worked_hours:.2f}," \
               f"\"{service.observations if service.observations else ''}\"," \
               f"\"{specific_tasks_csv}\"\n" # Enclose observations and specific tasks in quotes
        si.write(line.encode('utf-8'))
    
    si.seek(0)
    return send_file(si,
                     mimetype='text/csv',
                     as_attachment=True,
                     download_name=f'servicios_{current_month_str}.csv')

@app.route("/download_pdf")
@login_required
def download_pdf():
    current_month_str = session.get('current_month', datetime.now().strftime('%Y-%m'))
    year, month = map(int, current_month_str.split('-'))

    services = Service.query.filter_by(user_id=current_user.id).filter(
        db.extract('year', Service.date) == year,
        db.extract('month', Service.date) == month
    ).order_by(Service.date.asc(), Service.entry_time.asc()).all()

    total_hours = sum(service.worked_hours for service in services)

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4)) # Changed to landscape A4
    styles = getSampleStyleSheet()
    
    # Custom style for table cells to handle long text
    styles.add(ParagraphStyle(name='TableContent', fontSize=7, leading=9, alignment=colors.TA_CENTER))
    styles.add(ParagraphStyle(name='TableContentLeft', fontSize=7, leading=9, alignment=colors.TA_LEFT))

    story = []

    # Title
    month_name = [
        "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
    ][month - 1]
    title_text = f"Reporte de Horas Trabajadas para el mes de {month_name} {year}"
    story.append(Paragraph(title_text, styles['h1']))
    story.append(Spacer(1, 0.2 * inch))

    # User Info
    story.append(Paragraph(f"Usuario: <b>{current_user.username}</b>", styles['Normal']))
    story.append(Spacer(1, 0.1 * inch))

    # Table Data
    # Removed 'Tareas Específicas' column
    data = [['Fecha', 'Lugar', 'Entrada', 'Descanso (min)', 'Salida', 'Horas', 'Observaciones']]
    for service in services:
        # Using Paragraph for observations to allow word wrapping
        obs_paragraph = Paragraph(service.observations if service.observations else '', styles['TableContentLeft'])

        data.append([
            Paragraph(service.date.strftime('%d/%m/%Y'), styles['TableContent']),
            Paragraph(service.place, styles['TableContentLeft']),
            Paragraph(service.entry_time.strftime('%H:%M'), styles['TableContent']),
            Paragraph(str(service.break_duration), styles['TableContent']),
            Paragraph(service.exit_time.strftime('%H:%M'), styles['TableContent']),
            Paragraph(f"{service.worked_hours:.2f}", styles['TableContent']),
            obs_paragraph # Use the Paragraph object
        ])

    # Define column widths for landscape A4 (297mm width)
    # Total width of page is ~11.69 inches. Let's use 10.5 inches for table width (756 points)
    # Distribute 10.5 inches among 7 columns
    # Fecha, Lugar, Entrada, Descanso, Salida, Horas, Observaciones
    # Weights: 1.0, 1.5, 1.0, 1.0, 1.0, 1.0, 3.0 (approximate proportions)
    # Total weight = 9.5
    # Let's define fixed widths in points for better control, summing up to a bit less than page width
    # A4 landscape width = 841.89 points. Let's aim for ~780 points total width.
    col_widths_pts = [
        60,  # Fecha (approx 21mm)
        120, # Lugar (approx 42mm)
        60,  # Entrada (approx 21mm)
        60,  # Descanso (approx 21mm)
        60,  # Salida (approx 21mm)
        60,  # Horas (approx 21mm)
        360  # Observaciones (approx 127mm - much wider for wrapping)
    ]
    
    table = Table(data, colWidths=col_widths_pts)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4CAF50')), # Header background
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke), # Header text color
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'), # Header alignment
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f2f2f2')), # Even rows background
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black), # Thinner grid lines
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('FONTSIZE', (0,0), (-1,-1), 7), # Smaller font for table content
        ('ALIGN', (0,0), (0,-1), 'CENTER'), # Fecha
        ('ALIGN', (2,0), (5,-1), 'CENTER'), # Entrada, Descanso, Salida, Horas
        ('ALIGN', (1,0), (1,-1), 'LEFT'), # Lugar
        ('ALIGN', (6,0), (6,-1), 'LEFT'), # Observaciones
    ]))
    story.append(table)
    story.append(Spacer(1, 0.2 * inch))

    # Total Hours
    story.append(Paragraph(f"<b>Total de Horas Trabajadas: {total_hours:.2f}</b>", styles['h2']))

    doc.build(story)
    buffer.seek(0)

    return send_file(buffer,
                     mimetype='application/pdf',
                     as_attachment=True,
                     download_name=f'resumen_servicios_{current_month_str}.pdf')


# Tareas Específicas (Summary)
@app.route("/tasks_summary")
@login_required
def tasks_summary():
    current_tasks_month_str = session.get('current_tasks_month', datetime.now().strftime('%Y-%m'))
    year, month = map(int, current_tasks_month_str.split('-'))

    services = Service.query.filter_by(user_id=current_user.id).filter(
        db.extract('year', Service.date) == year,
        db.extract('month', Service.date) == month
    ).all()

    tasks_summary_data = defaultdict(float)
    for service in services:
        if service.specific_tasks:
            try:
                specific_tasks = json.loads(service.specific_tasks)
                for task in specific_tasks:
                    description = task.get('description')
                    duration = task.get('duration')
                    if description and isinstance(duration, (int, float)):
                        tasks_summary_data[description] += duration
            except json.JSONDecodeError:
                # Handle cases where specific_tasks might be malformed JSON
                print(f"Warning: Could not decode specific_tasks for service ID {service.id}: {service.specific_tasks}")
                flash(f"Advertencia: Error al leer tareas específicas para el servicio del {service.date}. Contacta con soporte si persiste.", 'warning')

    spanish_month_names = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]

    return render_template('tasks.html',
                           tasks_summary=tasks_summary_data,
                           current_month=current_tasks_month_str,
                           spanish_month_names=spanish_month_names,
                           current_username=current_user.username)

@app.route("/load_tasks_month", methods=['POST'])
@login_required
def load_tasks_month():
    selected_month = request.form.get('selected_month')
    if selected_month:
        session['current_tasks_month'] = selected_month
    return redirect(url_for('tasks_summary'))

@app.route("/generate_tasks_pdf")
@login_required
def generate_tasks_pdf():
    current_tasks_month_str = session.get('current_tasks_month', datetime.now().strftime('%Y-%m'))
    year, month = map(int, current_tasks_month_str.split('-'))

    services = Service.query.filter_by(user_id=current_user.id).filter(
        db.extract('year', Service.date) == year,
        db.extract('month', Service.date) == month
    ).all()

    tasks_summary_data = defaultdict(float)
    for service in services:
        if service.specific_tasks:
            try:
                specific_tasks = json.loads(service.specific_tasks)
                for task in specific_tasks:
                    description = task.get('description')
                    duration = task.get('duration')
                    if description and isinstance(duration, (int, float)):
                        tasks_summary_data[description] += duration
            except json.JSONDecodeError:
                print(f"Warning: Could not decode specific_tasks for service ID {service.id} during PDF generation.")

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=portrait(A4)) # Portrait A4 for tasks summary
    styles = getSampleStyleSheet()
    story = []

    # Title
    month_name = [
        "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
    ][month - 1]
    title_text = f"Resumen de Horas por Tarea Específica - {month_name} {year}"
    story.append(Paragraph(title_text, styles['h1']))
    story.append(Spacer(1, 0.2 * inch))

    # User Info
    story.append(Paragraph(f"Usuario: <b>{current_user.username}</b>", styles['Normal']))
    story.append(Spacer(1, 0.1 * inch))

    # Table Data
    data = [['Tarea Específica', 'Total Horas']]
    for task, total_hours in tasks_summary_data.items():
        data.append([task, f"{total_hours:.2f}"])

    if not tasks_summary_data:
        story.append(Paragraph("No hay datos de tareas específicas para este mes.", styles['Normal']))
    else:
        # Adjust column widths for portrait A4
        # A4 portrait width = 595.27 points. Let's aim for ~550 points total width.
        col_widths_pts = [
            400, # Tarea Específica
            150  # Total Horas
        ]

        table = Table(data, colWidths=col_widths_pts)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4CAF50')), # Header background
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke), # Header text color
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f2f2f2')), # Even rows background
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('BOX', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('FONTSIZE', (0,0), (-1,-1), 10), # Font size for table content
        ]))
        story.append(table)
    
    doc.build(story)
    buffer.seek(0)

    return send_file(buffer,
                     mimetype='application/pdf',
                     as_attachment=True,
                     download_name=f'resumen_tareas_especificas_{current_tasks_month_str}.pdf')


# Database Initialization (for local development or initial setup)
def create_db():
    with app.app_context():
        db.create_all()
        # Create default 'admin' user if it doesn't exist
        if not User.query.filter_by(username='admin').first():
            admin_user = User(username='admin')
            admin_user.set_password('password123') # CHANGE THIS FOR PRODUCTION!
            db.session.add(admin_user)
            db.session.commit()
            print("Default 'admin' user created.")
        else:
            print("Default 'admin' user already exists.")
        print("Database initialized successfully.")

if __name__ == '__main__':
    # This block is typically for local development.
    # For Render, the 'init_db.py' script handles database creation.
    # You can uncomment create_db() if you run app.py directly for local testing
    # and want to ensure tables are created/admin user exists.
    # create_db() 
    app.run(debug=True)