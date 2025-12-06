from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, datetime, timedelta
import os, math, io, csv
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle, SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///finance.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ---- Models ----
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(100))
    occupation = db.Column(db.String(100))
    monthly_income = db.Column(db.Numeric(12,2), default=0)
    current_savings = db.Column(db.Numeric(12,2), default=0)
    is_admin = db.Column(db.Boolean, default=False)

    expenses = db.relationship('Expense', backref='user', lazy=True, cascade="all, delete-orphan")
    goals = db.relationship('Goal', backref='user', lazy=True, cascade="all, delete-orphan")

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

class Expense(db.Model):
    __tablename__ = 'expenses'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(255))
    category = db.Column(db.String(100))
    amount = db.Column(db.Numeric(12,2), nullable=False)
    frequency = db.Column(db.Enum('daily','monthly','yearly'), nullable=False)
    description = db.Column(db.String(500))
    date_recorded = db.Column(db.Date, default=date.today)

class Goal(db.Model):
    __tablename__ = 'goals'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    target_amount = db.Column(db.Numeric(12, 2), nullable=False)
    date_created = db.Column(db.Date, default=date.today)
    # numeric priority (1 = highest). Default 5 if not provided.
    priority = db.Column(db.Integer, default=5, nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ---- Helpers ----
def monthly_expense_total(user):
    """
    Returns: (monthly_total_as_float, categories_dict)
    Converts daily/yearly frequencies into monthly equivalents.
    """
    expenses = Expense.query.filter_by(user_id=user.id).all()
    total = 0.0
    categories = {}
    for e in expenses:
        amt = float(e.amount)
        if e.frequency == 'daily':
            m = amt * 30.0
        elif e.frequency == 'yearly':
            m = amt / 12.0
        else:
            m = amt
        total += m
        key = e.category or 'Other'
        categories.setdefault(key, 0.0)
        categories[key] += m
    return total, categories

def predict_goals_sequential(user, monthly_saving, current_savings):
    """
    Sequential prediction algorithm using numeric priorities:
    - Sort goals by numeric priority ascending (1 = highest), then by creation date.
    - Use current_savings as immediate pot applied to the first goals in order.
    - For remaining amount, compute fractional months needed = remaining / monthly_saving.
      Convert months to days using average month length (30.44 days).
    - Next goal's start_date = previous goal end_date.
    Returns list of dicts with start_date, end_date, status, progress_percent.
    """
    results = []
    # Fetch goals and sort by numeric priority then date_created then id
    goals = sorted(
        Goal.query.filter_by(user_id=user.id).all(),
        key=lambda g: (g.priority if g.priority is not None else 9999, g.date_created, g.id)
    )

    start_dt = datetime.today()
    remaining_pot = float(current_savings or 0.0)
    avg_days_per_month = 30.44  # realistic month average

    # Snapshot to show how much of each goal is already covered by current savings (progress)
    original_savings = remaining_pot

    for g in goals:
        target = float(g.target_amount)
        # progress percent based on original_savings towards the specific goal (bounded to 100)
        if original_savings <= 0:
            progress_percent = 0.0
        else:
            progress_percent = min(100.0, (original_savings / target) * 100.0)

        if target <= 0:
            end_dt = start_dt
            status = "Invalid target"
        else:
            # If current pot covers the goal immediately:
            if remaining_pot >= target:
                end_dt = start_dt  # achieved now
                status = f"✅ You can afford '{g.title}' now!"
                remaining_pot -= target
            else:
                # use whatever remains from the pot
                still_needed = target - remaining_pot
                if monthly_saving <= 0:
                    end_dt = None
                    status = f"❌ Can't predict '{g.title}' (no monthly savings)."
                    remaining_pot = 0.0
                else:
                    months_needed = still_needed / monthly_saving  # fractional months allowed
                    days_needed = months_needed * avg_days_per_month
                    # ensure at least 1 day if something is needed
                    days_needed = max(1.0, days_needed)
                    end_dt = start_dt + timedelta(days=days_needed)
                    remaining_pot = 0.0
                    status = f"⏳ Predicted by {end_dt.strftime('%d-%m-%Y')}"
        results.append({
            'goal': g,
            'start_date': start_dt,
            'end_date': end_dt,
            'status': status,
            'progress_percent': round(progress_percent, 2),
            'priority': int(g.priority or 0)
        })
        # Next goal starts at end_dt (if end_dt is None, future goals cannot be predicted - keep same start)
        last_end = results[-1]['end_date']
        if last_end:
            # next goal starts right after this end
            start_dt = last_end
        else:
            # cannot predict further; keep start_dt unchanged (or break if you prefer)
            start_dt = last_end or start_dt

    return results


# ---- Routes ----
@app.route('/')
def home():
    return render_template('home.html')

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        email = request.form['email'].lower().strip()
        pw = request.form['password']
        name = request.form.get('name','').strip()
        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'danger')
            return redirect(url_for('register'))
        u = User(email=email, name=name)
        u.set_password(pw)
        db.session.add(u)
        db.session.commit()
        flash('Registered! Login now.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        email = request.form['email'].lower().strip()
        pw = request.form['password']
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(pw):
            login_user(user)
            flash('Logged in', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid credentials', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out', 'info')
    return redirect(url_for('home'))

# ---- Admin routes ----
@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        pw = request.form['password']
        if username == "sahil" and pw == "1234":
            session['is_admin'] = True
            flash('Admin logged in', 'success')
            return redirect(url_for('admin_panel'))
        user = User.query.filter_by(email=username).first()
        if user and user.check_password(pw) and user.is_admin:
            login_user(user)
            session['is_admin'] = True
            flash('Admin user logged in', 'success')
            return redirect(url_for('admin_panel'))
        flash('Invalid admin credentials', 'danger')
    return render_template('admin_login.html')

@app.route('/admin/panel')
def admin_panel():
    if not session.get('is_admin'):
        flash('Unauthorized', 'danger')
        return redirect(url_for('admin_login'))
    users = User.query.all()
    return render_template('admin_panel.html', users=users)

@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    if not session.get('is_admin'):
        flash('Unauthorized', 'danger')
        return redirect(url_for('admin_login'))
    user = User.query.get_or_404(user_id)
    try:
        db.session.delete(user)
        db.session.commit()
        flash(f'User {user.email} deleted successfully.', 'success')
    except:
        db.session.rollback()
        flash('Error deleting user.', 'danger')
    return redirect(url_for('admin_panel'))

# ---- Dashboard ----
@app.route('/dashboard', methods=['GET','POST'])
@login_required
def dashboard():
    user = current_user
    monthly_total, categories = monthly_expense_total(user)
    expense_labels = list(categories.keys())
    expense_values = list(categories.values())

    expenses = Expense.query.filter_by(user_id=user.id).order_by(Expense.date_recorded.desc()).all()
    goals = Goal.query.filter_by(user_id=user.id).order_by(Goal.priority, Goal.date_created).all()

    monthly_income = float(user.monthly_income or 0)
    current_savings = float(user.current_savings or 0)
    monthly_savings = monthly_income - monthly_total

    warning = "⚠️ Expenses exceed income!" if monthly_total > monthly_income else ""

    # Use the sequential prediction helper
    predicted_goals = predict_goals_sequential(user, monthly_savings, current_savings)

    # --- Loan Calculator ---
    loan_result = None
    if request.method == 'POST' and 'loan_submit' in request.form:
        try:
            principal = float(request.form.get('principal', 0))
            annual_rate = float(request.form.get('annual_rate', 0))
            years = float(request.form.get('years', 0))

            monthly_rate = annual_rate / 12 / 100
            n_months = years * 12
            emi = 0
            total_payment = 0
            total_interest = 0

            if monthly_rate > 0:
                emi = principal * monthly_rate * (1 + monthly_rate) ** n_months / ((1 + monthly_rate) ** n_months - 1)
            else:
                emi = principal / n_months

            total_payment = emi * n_months
            total_interest = total_payment - principal

            loan_result = {
                'emi': round(emi,2),
                'total_payment': round(total_payment,2),
                'total_interest': round(total_interest,2)
            }
        except:
            flash('Invalid loan input.', 'danger')

    return render_template('dashboard.html',
                           monthly_income=monthly_income,
                           monthly_expenses=monthly_total,
                           categories=categories,
                           current_savings=current_savings,
                           user=user,
                           expenses=expenses,
                           expense_labels=expense_labels,
                           expense_values=expense_values,
                           warning=warning,
                           goals=goals,
                           predicted_goals=predicted_goals,
                           loan_result=loan_result)


# ---- Add / Delete Goal & Expense ----
@app.route('/goal/add', methods=['POST'])
@login_required
def add_goal():
    title = request.form.get('goal_title', '').strip()
    try:
        target = float(request.form.get('goal_amount', 0))
    except:
        target = 0.0
    # parse priority (integer). Default to 5 if missing or invalid.
    try:
        priority = int(request.form.get('priority', 5))
    except:
        priority = 5

    if not title or target <= 0:
        flash('Please enter a valid goal and amount.', 'warning')
        return redirect(url_for('dashboard'))
    try:
        goal = Goal(user_id=current_user.id, title=title, target_amount=target, priority=priority)
        db.session.add(goal)
        db.session.commit()
        flash('Goal added successfully!', 'success')
    except Exception as ex:
        db.session.rollback()
        flash('Error adding goal: ' + str(ex), 'danger')
    return redirect(url_for('dashboard'))

@app.route('/goal/delete/<int:goal_id>', methods=['POST'])
@login_required
def delete_goal(goal_id):
    goal = Goal.query.filter_by(id=goal_id, user_id=current_user.id).first()
    if not goal:
        flash('Goal not found.', 'danger')
        return redirect(url_for('dashboard'))
    try:
        db.session.delete(goal)
        db.session.commit()
        flash('Goal deleted!', 'warning')
    except:
        db.session.rollback()
        flash('Error deleting goal.', 'danger')
    return redirect(url_for('dashboard'))

@app.route('/expenses/add', methods=['POST'])
@login_required
def add_expense():
    try:
        title = request.form.get('title','').strip()
        category = request.form.get('category','Other').strip()
        amount = float(request.form.get('amount',0))
        frequency = request.form.get('frequency','monthly')
        desc = request.form.get('description','')
        e = Expense(user_id=current_user.id, title=title, category=category, amount=amount, frequency=frequency, description=desc)
        db.session.add(e)
        db.session.commit()
        flash('Expense added', 'success')
    except Exception as ex:
        db.session.rollback()
        flash('Error adding expense: ' + str(ex), 'danger')
    return redirect(url_for('dashboard'))

@app.route('/expenses/delete/<int:expense_id>', methods=['POST'])
@login_required
def delete_expense(expense_id):
    expense = Expense.query.filter_by(id=expense_id, user_id=current_user.id).first()
    if not expense:
        flash('Expense not found', 'danger')
        return redirect(url_for('dashboard'))
    try:
        db.session.delete(expense)
        db.session.commit()
        flash('Expense deleted!', 'warning')
    except:
        db.session.rollback()
        flash('Error deleting expense', 'danger')
    return redirect(url_for('dashboard'))


# ---- Export CSV / PDF ----
@app.route('/export_csv')
@login_required
def export_csv():
    si = io.StringIO()
    cw = csv.writer(si)

    # Expenses
    cw.writerow(['Expenses'])
    cw.writerow(['Title', 'Amount', 'Frequency', 'Description', 'Date'])
    expenses = Expense.query.filter_by(user_id=current_user.id).all()
    for e in expenses:
        cw.writerow([e.title, float(e.amount), e.frequency, e.description or '', e.date_recorded.strftime('%d-%m-%Y')])

    # Goals with predictions (use same helper)
    cw.writerow([])
    cw.writerow(['Goals'])
    cw.writerow(['Title', 'Target Amount', 'Date Created', 'Priority', 'Predicted Completion', 'Status'])

    monthly_income = float(current_user.monthly_income or 0)
    current_savings = float(current_user.current_savings or 0)
    monthly_total, _ = monthly_expense_total(current_user)
    monthly_savings = monthly_income - monthly_total

    predicted = predict_goals_sequential(current_user, monthly_savings, current_savings)
    for p in predicted:
        g = p['goal']
        pred_end = p['end_date'].strftime('%d-%m-%Y') if p['end_date'] else 'N/A'
        cw.writerow([g.title, float(g.target_amount), g.date_created.strftime('%d-%m-%Y'), int(g.priority or 0), pred_end, p['status']])

    output = io.BytesIO()
    output.write(si.getvalue().encode('utf-8'))
    output.seek(0)
    return send_file(output, mimetype='text/csv', as_attachment=True, download_name='dashboard.csv')


@app.route('/export_pdf')
@login_required
def export_pdf():
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []
    styles = getSampleStyleSheet()
    elements.append(Paragraph(f"{current_user.name}'s Dashboard", styles['Title']))

    # Expenses Table
    expenses = Expense.query.filter_by(user_id=current_user.id).all()
    data = [['Title','Amount','Frequency','Description','Date']]
    for e in expenses:
        data.append([e.title, float(e.amount), e.frequency, e.description or '', e.date_recorded.strftime('%d-%m-%Y')])
    t=Table(data, hAlign='LEFT')
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.HexColor("#0d6efd")),
        ('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('GRID',(0,0),(-1,-1),1,colors.black),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
        ('ROWBACKGROUNDS',(1,1),(-1,-1),[colors.whitesmoke, colors.lightgrey])
    ]))
    elements.append(Paragraph("Expenses", styles['Heading2']))
    elements.append(t)

    # Goals Table with predicted completion from helper
    monthly_income = float(current_user.monthly_income or 0)
    current_savings = float(current_user.current_savings or 0)
    monthly_total, _ = monthly_expense_total(current_user)
    monthly_savings = monthly_income - monthly_total

    predicted = predict_goals_sequential(current_user, monthly_savings, current_savings)
    data = [['Title','Target Amount','Date Created','Priority','Predicted Completion','Status']]
    for p in predicted:
        g = p['goal']
        pred_end = p['end_date'].strftime('%d-%m-%Y') if p['end_date'] else 'N/A'
        data.append([g.title, float(g.target_amount), g.date_created.strftime('%d-%m-%Y'), int(g.priority or 0), pred_end, p['status']])

    t=Table(data, hAlign='LEFT')
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.HexColor("#198754")),
        ('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('GRID',(0,0),(-1,-1),1,colors.black),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
        ('ROWBACKGROUNDS',(1,1),(-1,-1),[colors.whitesmoke, colors.lightgrey])
    ]))
    elements.append(Paragraph("Goals", styles['Heading2']))
    elements.append(t)

    doc.build(elements)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name='dashboard.pdf', mimetype='application/pdf')


# ---- Clear Dashboard ----
@app.route('/dashboard/clear', methods=['POST'])
@login_required
def clear_dashboard():
    try:
        Expense.query.filter_by(user_id=current_user.id).delete()
        Goal.query.filter_by(user_id=current_user.id).delete()
        db.session.commit()
        flash('All your dashboard data cleared.', 'info')
    except:
        db.session.rollback()
        flash('Error clearing dashboard data', 'danger')
    return redirect(url_for('dashboard'))

# ---- Profile Update ----
@app.route('/profile/update', methods=['POST'])
@login_required
def update_profile():
    occ = request.form.get('occupation','').strip()
    income = request.form.get('monthly_income') or 0
    savings = request.form.get('current_savings') or 0
    try:
        current_user.occupation = occ
        current_user.monthly_income = float(income)
        current_user.current_savings = float(savings)
        db.session.commit()
        flash('Profile updated', 'success')
    except:
        db.session.rollback()
        flash('Error updating profile', 'danger')
    return redirect(url_for('dashboard'))

# ---- About & Review ----
@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/review')
def review():
    return render_template('review.html')

# ---- Run ----
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
