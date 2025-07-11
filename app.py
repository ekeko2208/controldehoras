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
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.units import inch

# Initialize Flask App
app = Flask(__name__)

# Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key_here') # Use environment variable for production
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
    username = db.Column(db.String(20), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
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

# Routes
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
                           current_username=current_user.username,
                           search_query=search_query)

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
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            flash('El nombre de usuario ya existe. Por favor, elige otro.', 'danger')
            return redirect(url_for('register'))
        
        new_user = User(username=username)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        flash('Registro exitoso! Ahora puedes iniciar sesión.', 'success')
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
        new_password = request.form.get('password')
        
        if new_username != current_user.username:
            existing_user = User.query.filter_by(username=new_username).first()
            if existing_user and existing_user.id != current_user.id:
                flash('Este nombre de usuario ya está en uso.', 'danger')
                return redirect(url_for('profile'))
            current_user.username = new_username
            flash('Nombre de usuario actualizado!', 'success')
        
        if new_password:
            current_user.set_password(new_password)
            flash('Contraseña actualizada!', 'success')
        
        try:
            db.session.commit()
            flash('Perfil actualizado exitosamente!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error al actualizar el perfil: {e}', 'danger')
        
        return redirect(url_for('profile'))
    
    return render_template('profile.html', current_username=current_user.username)

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
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []

    # Title
    month_name = [
        "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
    ][month - 1]
    title_text = f"Resumen de Servicios - {month_name} {year}"
    story.append(Paragraph(title_text, styles['h1']))
    story.append(Spacer(1, 0.2 * inch))

    # User Info
    story.append(Paragraph(f"Usuario: <b>{current_user.username}</b>", styles['Normal']))
    story.append(Spacer(1, 0.1 * inch))

    # Table Data
    data = [['Fecha', 'Lugar', 'Entrada', 'Descanso (min)', 'Salida', 'Horas', 'Observaciones', 'Tareas Específicas']]
    for service in services:
        specific_tasks_str = ""
        if service.specific_tasks:
            try:
                tasks = json.loads(service.specific_tasks)
                task_strings = []
                for task in tasks:
                    task_strings.append(f"{task.get('description', 'N/A')} ({task.get('duration', 0):.2f}h)")
                specific_tasks_str = "; ".join(task_strings)
            except json.JSONDecodeError:
                specific_tasks_str = "Error al cargar tareas"

        data.append([
            service.date.strftime('%d/%m/%Y'),
            service.place,
            service.entry_time.strftime('%H:%M'),
            str(service.break_duration),
            service.exit_time.strftime('%H:%M'),
            f"{service.worked_hours:.2f}",
            service.observations if service.observations else '',
            specific_tasks_str
        ])

    table = Table(data)
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
        ('FONTSIZE', (0,0), (-1,-1), 8), # Smaller font for table content
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
    doc = SimpleDocTemplate(buffer, pagesize=letter)
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
        table = Table(data)
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


# Database Initialization
def create_db():
    with app.app_context():
        db.create_all()
        print("Database created!")

if __name__ == '__main__':
    # Call create_db() only if you need to create the database tables
    # For Render, this is usually handled by a build step or entrypoint command
    # create_db() 
    app.run(debug=True)